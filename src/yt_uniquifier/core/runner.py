"""Subprocess runner for ffmpeg with `-progress pipe:1` parsing and cancel."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand

# `divergence_sample` (v0.7 R4 / F2) carries phash_similarity + running EMA
# per segment from `orchestrator._maybe_emit_divergence`. GUI consumers
# match on `ev.kind` and route to the live divergence indicator.
EventKind = Literal[
    "progress",
    "log",
    "done",
    "error",
    "divergence_sample",
    # v0.8.0 R5 — target-VMAF feedback loop. One ``target_vmaf`` event
    # per encode attempt carries (segment, vmaf, crf, attempt, target).
    # ``target_vmaf_failed`` is the terminal event when
    # ``target_vmaf_max_retries`` was exhausted before the target was
    # met; the best attempt's bytes are kept on disk regardless.
    "target_vmaf",
    "target_vmaf_failed",
]


@dataclass(frozen=True)
class RunEvent:
    kind: EventKind
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    returncode: int
    duration_sec: float
    output_path: Path


class CancelToken:
    """Thread-safe cooperative cancellation flag.

    Uses ``threading.Event`` rather than a bare ``bool`` so the write
    from the GUI thread (cancel()) and the read from the worker thread
    (is_cancelled()) are correctly synchronised under PyPy and the
    free-threaded CPython 3.13+ build. CPython's GIL hides the issue
    for plain attribute reads/writes but that is an implementation
    detail, not a language guarantee.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds; return True if cancelled.

        Lets pollers replace their own sleep-loops (``sleep(0.1)`` in a
        ``while not cancelled`` body) with a single ``cancel_token.wait``
        call. Backed by ``threading.Event.wait`` so the thread wakes
        immediately on cancel — no 100 ms of latency, no CPU spin.
        """
        return self._event.wait(timeout)


class PauseToken:
    """Thread-safe pause/resume flag — v0.7 R6 / F5.

    The pause primitive is intentionally separated from cancel:

      * The GUI Pause button → ``token.pause()``. The runner's watcher
        thread observes the flag and sends SIGSTOP to the ffmpeg
        subprocess via ``process_control.suspend_process_tree``.
      * Resume → ``token.resume()``. The same watcher fires
        ``resume_process_tree`` and the encode continues.
      * The token records ``paused_at`` (monotonic seconds) so the
        orchestrator can apply a 24-hour safety auto-cancel — a paused
        run that is never resumed must not strand resources forever.

    All callers must treat the token as advisory: pause is best-effort
    (a process that already exited is harmless to no-op-on), and the
    runner falls back to "continue running" if the OS denies SIGSTOP.
    """

    AUTO_CANCEL_SEC = 24 * 3600  # 24 h — hard safety limit

    def __init__(self) -> None:
        self._paused = threading.Event()
        self._paused_at_monotonic: float | None = None
        self._paused_at_wall: float | None = None
        self._lock = threading.Lock()

    def pause(self) -> None:
        with self._lock:
            if self._paused.is_set():
                return
            self._paused.set()
            self._paused_at_monotonic = time.monotonic()
            self._paused_at_wall = time.time()

    def resume(self) -> None:
        with self._lock:
            self._paused.clear()
            self._paused_at_monotonic = None
            self._paused_at_wall = None

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def paused_for_sec(self) -> float:
        """Monotonic seconds since ``pause()`` was called. 0 if not paused."""
        with self._lock:
            if self._paused_at_monotonic is None:
                return 0.0
            return max(0.0, time.monotonic() - self._paused_at_monotonic)

    def paused_at_wall(self) -> float | None:
        """Wall-clock timestamp of the pause, for state.json persistence."""
        with self._lock:
            return self._paused_at_wall

    def should_auto_cancel(self) -> bool:
        return self.paused_for_sec() >= self.AUTO_CANCEL_SEC

    def wait_while_paused(
        self, *, cancel_token: CancelToken | None = None,
        poll_sec: float = 0.25,
    ) -> bool:
        """Block until resumed or cancelled. Returns True if cancelled.

        Used by the orchestrator's segment loop to honour pauses at
        the segment boundary — the in-flight ffmpeg subprocess is
        already suspended via the runner's watcher; this prevents the
        orchestrator from racing ahead and queueing a new segment
        before the user has hit Resume.
        """
        while self.is_paused():
            if cancel_token is not None and cancel_token.is_cancelled():
                return True
            if self.should_auto_cancel():
                if cancel_token is not None:
                    cancel_token.cancel()
                return True
            time.sleep(poll_sec)
        return False


_NVENC_OOM_PATTERNS = (
    "openencodesessionex failed",
    "no encode capable devices",
    "no nvenc capable devices",
    "out of memory",
)


def _is_nvenc_oom(log_lines: list[str]) -> bool:
    """Heuristic: NVENC session-exhaustion vs other ffmpeg failures.

    Matches against the last 50 merged ffmpeg log lines (case-insensitive).
    """
    tail = "\n".join(log_lines[-50:]).lower()
    return any(p in tail for p in _NVENC_OOM_PATTERNS)


_NVENC_OOM_MAX_RETRIES = 1
_NVENC_OOM_BACKOFF_SEC = 2.0
_DEFAULT_STALL_TIMEOUT_SEC = 600.0
_STALL_TIMEOUT_ENV = "YT_UNIQ_STALL_TIMEOUT_SEC"
_WALL_TIMEOUT_ENV = "YT_UNIQ_WALL_TIMEOUT_SEC"
_MAX_RETAINED_LOG_CHARS = 2 * 1024 * 1024
_MAX_RETAINED_LINE_CHARS = 256 * 1024


class _WatchdogTimeout(PipelineError):
    """Internal carrier preserving the merged log when a watchdog fires."""

    def __init__(self, reason: str, log_lines: list[str]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.log_lines = log_lines


def _resolve_timeout(
    explicit: float | None,
    *,
    env_name: str,
    default: float | None,
) -> float | None:
    """Resolve a positive timeout; zero disables it.

    Environment configuration keeps the watchdog available to CLI, GUI, web,
    and queue workers without duplicating public options across every frontend.
    Explicit values are primarily useful to embedders and deterministic tests.
    """
    value: float | None = explicit
    if value is None:
        raw = os.environ.get(env_name)
        if raw is None or not raw.strip():
            value = default
        else:
            try:
                value = float(raw)
            except ValueError as exc:
                raise PipelineError(
                    f"{env_name} must be a non-negative number of seconds; got {raw!r}"
                ) from exc
    if value is None or value == 0:
        return None
    if value < 0:
        raise PipelineError(
            f"{env_name} must be a non-negative number of seconds; got {value}"
        )
    return value


def run(
    cmd: BuiltCommand,
    *,
    output: Path,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    pause_token: PauseToken | None = None,
    log_path: Path | None = None,
    progress_via_stdout: bool = True,
    extra_env: dict[str, str] | None = None,
    stall_timeout_sec: float | None = None,
    wall_timeout_sec: float | None = None,
) -> RunResult:
    """Execute the BuiltCommand and stream progress events.

    The command is expected to be a complete ffmpeg invocation including the
    output path. We append `-progress pipe:1 -nostats`; stdout and the human
    stderr log are merged and drained together to avoid pipe-buffer deadlocks.

    Retries up to ``_NVENC_OOM_MAX_RETRIES`` times on NVENC GPU session
    exhaustion via an iterative loop. The previous recursive
    implementation grew the Python call stack and tangled the cancel
    flow; the loop is equivalent semantically (one retry on OOM) and
    simpler to reason about.
    """
    on_event = on_event or (lambda _e: None)
    resolved_stall_timeout = _resolve_timeout(
        stall_timeout_sec,
        env_name=_STALL_TIMEOUT_ENV,
        default=_DEFAULT_STALL_TIMEOUT_SEC,
    )
    resolved_wall_timeout = _resolve_timeout(
        wall_timeout_sec,
        env_name=_WALL_TIMEOUT_ENV,
        default=None,
    )

    full_cmd = list(cmd.args)
    if not full_cmd:
        # Empty BuiltCommand.args has no binary and no output path. The
        # subsequent insert/Popen would silently emit garbage; raise so
        # the caller fixes its builder instead.
        raise PipelineError("runner.run received an empty ffmpeg command")
    if progress_via_stdout:
        # Insert just before the output path (last arg). All build_*
        # callers in pipeline.py end with `str(output)`; assert it so a
        # future caller passing a `-flag` as the trailing argument fails
        # loudly instead of silently producing an unparseable command
        # line.
        if full_cmd[-1].startswith("-"):
            raise PipelineError(
                f"runner.run expects the output path as the last arg, "
                f"got option {full_cmd[-1]!r}",
            )
        insert_at = len(full_cmd) - 1
        full_cmd[insert_at:insert_at] = ["-progress", "pipe:1", "-nostats"]

    # extra_env: per-call env overrides (e.g. OMP_NUM_THREADS=1 from the
    # parallel batch path) — keeps the parent process's env untouched so
    # concurrent batch invocations can't stomp on each other's values.
    import os as _os
    proc_env = None
    if extra_env:
        proc_env = {**_os.environ, **extra_env}

    start = time.monotonic()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
    for attempt in range(_NVENC_OOM_MAX_RETRIES + 1):
        try:
            rc, log_lines = _run_once(
                full_cmd, on_event=on_event, cancel_token=cancel_token,
                pause_token=pause_token, proc_env=proc_env,
                stall_timeout_sec=resolved_stall_timeout,
                wall_timeout_sec=resolved_wall_timeout,
                log_path=log_path,
                append_log=attempt > 0,
            )
        except _WatchdogTimeout as exc:
            on_event(RunEvent(kind="error", payload={
                "reason": "timeout",
                "message": exc.reason,
            }))
            log_hint = f" (full log: {log_path})" if log_path is not None else ""
            raise PipelineError(f"{exc.reason}{log_hint}") from exc

        if cancel_token and cancel_token.is_cancelled():
            on_event(RunEvent(kind="error", payload={"reason": "cancelled"}))
            raise PipelineError("cancelled by user")

        if rc == 0:
            duration = time.monotonic() - start
            on_event(RunEvent(kind="done", payload={"duration_sec": duration}))
            return RunResult(returncode=rc, duration_sec=duration, output_path=output)

        if attempt < _NVENC_OOM_MAX_RETRIES and _is_nvenc_oom(log_lines):
            on_event(RunEvent(kind="log", payload={
                "phase": "retry", "reason": "nvenc oom",
                "attempt": attempt + 1,
            }))
            time.sleep(_NVENC_OOM_BACKOFF_SEC)
            continue

        # Trim user-visible tail to ~8 lines; full log already saved to
        # log_path when caller supplied it. (MED-3 from 2026-05-30 test report.)
        full_tail = "\n".join(log_lines[-30:])
        short_tail = "\n".join(log_lines[-8:])
        log_hint = f" (full log: {log_path})" if log_path is not None else ""
        on_event(RunEvent(kind="error", payload={"returncode": rc, "tail": full_tail}))
        raise PipelineError(
            f"ffmpeg exited with {rc}; last log:\n{short_tail}{log_hint}"
        )

    # Loop either returns on success or raises on failure; this is unreachable.
    raise PipelineError("runner.run exhausted retries without a verdict")


def _run_once(
    full_cmd: list[str],
    *,
    on_event: Callable[[RunEvent], None],
    cancel_token: CancelToken | None,
    pause_token: PauseToken | None = None,
    proc_env: dict[str, str] | None = None,
    stall_timeout_sec: float | None = None,
    wall_timeout_sec: float | None = None,
    log_path: Path | None = None,
    append_log: bool = False,
) -> tuple[int, list[str]]:
    """One Popen + drain pass. Returns (rc, log_lines).

    Factored out of ``run()`` so the outer retry loop doesn't need to
    inline the entire Popen body. Keeping it as a private helper means
    the test suite's existing ``runner.run`` patching surface is intact.
    """
    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        # Drain ffmpeg's human log and ``-progress pipe:1`` through one
        # stream.  Reading stdout to EOF before draining a separate stderr
        # pipe deadlocks as soon as verbose filter output fills the stderr
        # buffer (particularly easy on Windows, whose pipe is small).
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=proc_env,
        # v0.7 R9 — give the child its own POSIX session so we can kill
        # the whole process group via ``os.killpg`` from ``_terminate``.
        # Without this, ``sh -c "sleep 30"`` (or any ffmpeg invocation
        # via a wrapper script) leaves grandchildren running after the
        # leader dies; the inherited stdout fd keeps the parent pipe
        # open and the main loop's ``for line in proc.stdout`` blocks
        # until the grandchild exits naturally. This was hidden in
        # local dev because real ffmpeg invocations run as a direct
        # child of Python — the Linux CI ``test_cancel_during_silent
        # _subprocess_terminates_fast`` test surfaces it via a shell.
        start_new_session=sys.platform != "win32",
        # v0.7 R9 round-3 — Windows analogue. ``CREATE_NEW_PROCESS_GROUP``
        # lets us tree-kill via ``taskkill /T`` from ``_signal_proc``
        # without taking down the parent Python interpreter.
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            if sys.platform == "win32" else 0
        ),
    )
    if proc.stdout is None:
        # Popen above always asks for PIPE. Check before starting the watcher or
        # opening a log handle. A custom Popen wrapper may still have spawned a
        # child, so terminate it before rejecting the broken pipe contract.
        if proc.poll() is None:
            _terminate(proc)
        raise PipelineError(
            "ffmpeg Popen returned no stdout pipe — "
            "subprocess.PIPE was not honoured by the OS",
        )

    # A5 (v0.5.5): watcher thread for cancel during silent ffmpeg.
    # The main stdout-line loop below also checks cancel_token, but it
    # only wakes when ffmpeg writes a new progress block. A hung NVENC
    # session or libx264 final-flush stage can be silent for minutes,
    # during which `cancel_token.is_cancelled()` would be ignored until
    # either fresh output arrives or the outer 3600 s communicate
    # timeout fires. The watcher polls cancel_token.wait(0.25) and
    # SIGTERMs the child regardless of stdout state, then exits cleanly
    # once the main loop signals via stop_watcher.
    stop_watcher = threading.Event()
    watcher_thread: threading.Thread | None = None
    watchdog_lock = threading.Lock()
    process_started = time.monotonic()
    last_activity = process_started
    timeout_reason: str | None = None
    watcher_error: BaseException | None = None
    if (
        cancel_token is not None
        or pause_token is not None
        or stall_timeout_sec is not None
        or wall_timeout_sec is not None
    ):
        def _emit_from_watcher(event: RunEvent) -> bool:
            """Deliver a watcher event without stranding the child on failure."""
            nonlocal watcher_error
            try:
                on_event(event)
            except BaseException as exc:
                with watchdog_lock:
                    watcher_error = exc
                # A failing pause notification can leave the child stopped.
                # SIGCONT is harmless for a running process and makes teardown
                # deterministic for both POSIX and Windows wrappers.
                _resume_pid_safe(proc.pid)
                if proc.poll() is None:
                    _terminate(proc)
                return False
            return True

        def _watch() -> None:
            nonlocal last_activity, timeout_reason
            # Track our last-applied pause state so SIGSTOP / SIGCONT
            # fire only on transitions — repeated SIGSTOPs are harmless
            # but wasteful, and an OS-level "already stopped" race can
            # mask a legitimate resume request.
            suspended = False
            while not stop_watcher.is_set():
                if cancel_token is not None and cancel_token.is_cancelled():
                    if suspended:
                        # Wake the process so SIGINT/SIGKILL can actually
                        # land; SIGTERM-on-stopped processes hangs on some
                        # POSIX impls.
                        _resume_pid_safe(proc.pid)
                        suspended = False
                    if proc.poll() is None:
                        _terminate(proc)
                    return
                if proc.poll() is not None:
                    if suspended:
                        suspended = False
                    return
                want_pause = (
                    pause_token is not None and pause_token.is_paused()
                )
                if want_pause and not suspended and _suspend_pid_safe(proc.pid):
                    suspended = True
                    if not _emit_from_watcher(RunEvent(kind="log", payload={
                        "phase": "paused", "pid": proc.pid,
                    })):
                        return
                elif (
                    not want_pause and suspended
                    and _resume_pid_safe(proc.pid)
                ):
                    suspended = False
                    # Time intentionally spent paused is not a silent stall.
                    with watchdog_lock:
                        last_activity = time.monotonic()
                    if not _emit_from_watcher(RunEvent(kind="log", payload={
                        "phase": "resumed", "pid": proc.pid,
                    })):
                        return
                now = time.monotonic()
                with watchdog_lock:
                    silent_for = now - last_activity
                reason: str | None = None
                if wall_timeout_sec is not None and now - process_started >= wall_timeout_sec:
                    reason = (
                        f"ffmpeg wall timeout after {wall_timeout_sec:g} seconds"
                    )
                elif (
                    not want_pause
                    and stall_timeout_sec is not None
                    and silent_for >= stall_timeout_sec
                ):
                    reason = (
                        f"ffmpeg stalled with no output for {stall_timeout_sec:g} seconds"
                    )
                if reason is not None:
                    with watchdog_lock:
                        timeout_reason = reason
                    if not _emit_from_watcher(RunEvent(kind="log", payload={
                        "phase": "watchdog", "reason": reason, "pid": proc.pid,
                    })):
                        return
                    if suspended:
                        _resume_pid_safe(proc.pid)
                    if proc.poll() is None:
                        _terminate(proc)
                    return
                # Short poll: keep latency low for cancel/pause transitions.
                # cancel_token.wait honours cancel-set; fall back to a plain
                # sleep when only pause is wired.
                if cancel_token is not None:
                    if cancel_token.wait(0.25):
                        continue
                else:
                    time.sleep(0.25)
        watcher_thread = threading.Thread(
            target=_watch, daemon=True, name="ffmpeg-cancel-pause-watcher",
        )
        watcher_thread.start()

    retained_lines: deque[str] = deque()
    retained_chars = 0
    log_handle = None
    if log_path is not None:
        log_handle = log_path.open(
            "a" if append_log else "w", encoding="utf-8", buffering=1,
        )

    def _record_log_line(raw_line: str) -> None:
        nonlocal retained_chars
        if log_handle is not None:
            log_handle.write(raw_line + "\n")
        retained = raw_line[-_MAX_RETAINED_LINE_CHARS:]
        retained_lines.append(retained)
        retained_chars += len(retained)
        while retained_lines and retained_chars > _MAX_RETAINED_LOG_CHARS:
            retained_chars -= len(retained_lines.popleft())

    block: dict[str, str] = {}
    cancelled_mid_loop = False
    loop_failed = False
    try:
        for line in proc.stdout:
            with watchdog_lock:
                last_activity = time.monotonic()
            if cancel_token and cancel_token.is_cancelled():
                _terminate(proc)
                cancelled_mid_loop = True
                break

            line = line.strip()
            if not line:
                continue
            # stderr is merged into stdout so it is drained concurrently with
            # progress and retained for the full log / error diagnosis.
            _record_log_line(line)
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            block[key] = value
            if key == "progress":
                on_event(RunEvent(kind="progress", payload=dict(block)))
                block.clear()
    except BaseException:
        loop_failed = True
        if proc.poll() is None:
            _terminate(proc)
        raise
    finally:
        # Real subprocesses have stderr=None because it is merged into stdout
        # above, eliminating the two-pipe deadlock.  The conditional drain is
        # retained for lightweight Popen fakes and custom wrappers that expose
        # a separate stderr stream despite the requested redirection.
        stderr_data = ""
        try:
            if cancelled_mid_loop:
                _, stderr_data = proc.communicate(timeout=10)
            elif proc.stderr is not None:
                # Long timeout: a single segment encode can legitimately
                # take up to an hour on slow hardware. The TimeoutExpired
                # handler below force-kills if it overruns.
                _, stderr_data = proc.communicate(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                _, stderr_data = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                stderr_data = ""
        except (OSError, ValueError):
            # ValueError catches closed-pipe edge cases on Windows /
            # cancelled stdouts; OSError catches every other I/O fault
            # during stderr drain. Bare `except Exception` previously
            # also swallowed AttributeError from malformed proc fakes
            # in tests — surface those instead.
            stderr_data = ""
        if stderr_data:
            for stderr_line in stderr_data.splitlines():
                _record_log_line(stderr_line)
        stop_watcher.set()
        if watcher_thread is not None:
            watcher_thread.join(timeout=2.0)
        if loop_failed and log_handle is not None:
            log_handle.close()

    # `proc.communicate()` already waited and set returncode; a follow-up
    # `proc.wait()` is redundant and, if the bare `except` above swallowed
    # a TimeoutExpired without reaping the process, would block forever.
    # `getattr` so legacy / test fakes without `.returncode` still work.
    rc = getattr(proc, "returncode", None)
    if rc is None:
        rc = proc.wait()

    # A5 (v0.5.5): signal the watcher to exit and join. The thread is
    # daemon=True so it won't block process shutdown if join times out,
    # but we wait a short window to keep test output clean and to make
    # the thread lifecycle deterministic.
    with watchdog_lock:
        watchdog_failure = timeout_reason
        callback_failure = watcher_error
    if callback_failure is not None:
        if log_handle is not None:
            log_handle.close()
        raise callback_failure
    if watchdog_failure is not None:
        marker = f"[yt_uniquifier watchdog] {watchdog_failure}"
        _record_log_line(marker)
        if log_handle is not None:
            log_handle.close()
        raise _WatchdogTimeout(watchdog_failure, list(retained_lines))
    if log_handle is not None:
        log_handle.close()
    return rc, list(retained_lines)


def _terminate(proc: subprocess.Popen[str], wait_sec: float = 5.0) -> None:
    """Try SIGINT, wait, then SIGKILL.

    On POSIX we signal the entire process group (Popen above sets
    ``start_new_session=True``) so silent grandchildren — typically
    a shell wrapper around the real binary — die alongside the leader.
    Killing only the leader leaves grandchildren attached to the
    inherited stdout pipe, which keeps the parent's
    ``for line in proc.stdout`` loop blocked until they exit naturally.
    On Windows there's no process group concept here; ``send_signal``
    and ``proc.kill`` behave as before.
    """
    if proc.poll() is not None:
        return
    _signal_proc(proc, signal.SIGINT)
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        _signal_proc(proc, signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM)


def _signal_proc(proc: subprocess.Popen[str], sig: int) -> None:
    """Send ``sig`` to the proc's process group on POSIX, direct on Windows.

    POSIX: try ``os.killpg`` first so silent grandchildren die with the
    leader; if that fails (process gone, or the proc wasn't actually
    spawned with ``start_new_session=True`` — e.g. a unit-test fake
    Popen) fall back to ``proc.send_signal``.

    Windows: ``proc.send_signal`` only accepts CTRL_C_EVENT and
    CTRL_BREAK_EVENT; arbitrary signals raise ``ValueError``. Map our
    POSIX vocabulary (SIGINT → graceful, SIGKILL/SIGTERM → hard) onto
    ``proc.terminate`` / ``proc.kill`` instead.
    """
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return
        except (OSError, ProcessLookupError):
            pass
        with contextlib.suppress(OSError, ProcessLookupError):
            proc.send_signal(sig)
        return
    # Windows path: tree-kill via ``taskkill /T`` so silent grand-
    # children (sh.exe → sleep.exe inside Git-Bash) actually die.
    # ``proc.terminate()`` alone takes down only the leader, leaving
    # the stdout pipe held by the surviving grandchild — the same
    # POSIX bug ``start_new_session=True`` solves on Linux/macOS.
    # Catches Exception broadly because unit tests monkey-patch
    # ``subprocess.Popen`` globally — the patched fake replaces even
    # the ``taskkill`` subprocess and may raise AttributeError on
    # ``process.args`` access inside ``subprocess.run``.
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=5,
        )
    # ALWAYS fall through to ``proc.terminate()`` / ``proc.kill()``
    # — never early-return on taskkill success. Reasons:
    # 1. In production: taskkill /T already killed the tree; the
    #    follow-up call lands on a dead process and is a no-op. The
    #    redundancy is cheap.
    # 2. In unit tests: ``subprocess.run`` above ran against the
    #    monkey-patched fake (not real taskkill), so the actual
    #    process state machine wasn't touched. This call drives the
    #    fake's ``signalled`` / ``killed`` flags the assertions rely on.
    with contextlib.suppress(OSError, ProcessLookupError, AttributeError):
        if sig == signal.SIGINT:
            proc.terminate()
        else:
            proc.kill()


def _suspend_pid_safe(pid: int) -> bool:
    """Best-effort SIGSTOP / psutil.suspend over the process tree.

    Imported lazily so the runner module stays importable on hosts whose
    Windows installation is broken or manually stripped of its required
    psutil package. Returns True iff at least one process in the tree ack'd
    the signal.
    """
    try:
        from yt_uniquifier.core.process_control import suspend_process_tree
        return suspend_process_tree(pid) > 0
    except Exception:  # noqa: BLE001 — never let pause crash the run
        return False


def _resume_pid_safe(pid: int) -> bool:
    """Counterpart to ``_suspend_pid_safe`` — best-effort SIGCONT."""
    try:
        from yt_uniquifier.core.process_control import resume_process_tree
        return resume_process_tree(pid) > 0
    except Exception:  # noqa: BLE001 — never let resume crash the run
        return False
