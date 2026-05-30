"""Subprocess runner for ffmpeg with `-progress pipe:1` parsing and cancel."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand

EventKind = Literal["progress", "log", "done", "error"]


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


_NVENC_OOM_PATTERNS = (
    "openencodesessionex failed",
    "no encode capable devices",
    "no nvenc capable devices",
    "out of memory",
)


def _is_nvenc_oom(log_lines: list[str]) -> bool:
    """Heuristic: NVENC session-exhaustion vs other ffmpeg failures.

    Matches against the last 50 lines of stderr (case-insensitive).
    """
    tail = "\n".join(log_lines[-50:]).lower()
    return any(p in tail for p in _NVENC_OOM_PATTERNS)


def run(
    cmd: BuiltCommand,
    *,
    output: Path,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    log_path: Path | None = None,
    progress_via_stdout: bool = True,
    extra_env: dict[str, str] | None = None,
    _retried: bool = False,
) -> RunResult:
    """Execute the BuiltCommand and stream progress events.

    The command is expected to be a complete ffmpeg invocation including the
    output path. We append `-progress pipe:1 -nostats` so progress lines arrive
    on stdout while stderr carries human logs.
    """
    on_event = on_event or (lambda _e: None)

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

    start = time.monotonic()
    # extra_env: per-call env overrides (e.g. OMP_NUM_THREADS=1 from the
    # parallel batch path) — keeps the parent process's env untouched so
    # concurrent batch invocations can't stomp on each other's values.
    import os as _os
    proc_env = None
    if extra_env:
        proc_env = {**_os.environ, **extra_env}
    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=proc_env,
    )

    log_lines: list[str] = []
    assert proc.stdout is not None

    block: dict[str, str] = {}
    cancelled_mid_loop = False
    try:
        for line in proc.stdout:
            if cancel_token and cancel_token.is_cancelled():
                _terminate(proc)
                cancelled_mid_loop = True
                break

            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            block[key] = value
            if key == "progress":
                on_event(RunEvent(kind="progress", payload=dict(block)))
                block.clear()
    finally:
        # Drain stderr without blocking forever. After the stdout loop exits
        # ffmpeg may still be flushing its stderr summary; a raw
        # `proc.stderr.read()` is unbounded and hangs if ffmpeg exceeded the
        # OS pipe buffer (~64 KB on Linux) before stdout EOF. Use
        # `communicate(timeout=…)` on both branches — it drains both pipes
        # concurrently and waits for the child to exit. The outer
        # TimeoutExpired handler kills the process if it overstays.
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
            log_lines.extend(stderr_data.splitlines())

    # `proc.communicate()` already waited and set returncode; a follow-up
    # `proc.wait()` is redundant and, if the bare `except` above swallowed
    # a TimeoutExpired without reaping the process, would block forever.
    # `getattr` so legacy / test fakes without `.returncode` still work.
    rc = getattr(proc, "returncode", None)
    if rc is None:
        rc = proc.wait()
    duration = time.monotonic() - start

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if cancel_token and cancel_token.is_cancelled():
        on_event(RunEvent(kind="error", payload={"reason": "cancelled"}))
        raise PipelineError("cancelled by user")

    if rc != 0:
        # NVENC session exhaustion: another encode session has the GPU.
        # Wait a moment and retry exactly once before propagating.
        if not _retried and _is_nvenc_oom(log_lines):
            on_event(RunEvent(kind="log", payload={
                "phase": "retry", "reason": "nvenc oom",
            }))
            time.sleep(2.0)
            return run(
                cmd, output=output, on_event=on_event,
                cancel_token=cancel_token, log_path=log_path,
                progress_via_stdout=progress_via_stdout,
                extra_env=extra_env, _retried=True,
            )
        tail = "\n".join(log_lines[-30:])
        on_event(RunEvent(kind="error", payload={"returncode": rc, "tail": tail}))
        raise PipelineError(f"ffmpeg exited with {rc}; last log:\n{tail}")

    on_event(RunEvent(kind="done", payload={"duration_sec": duration}))
    return RunResult(returncode=rc, duration_sec=duration, output_path=output)


def _terminate(proc: subprocess.Popen[str], wait_sec: float = 5.0) -> None:
    """Try SIGINT, wait, then SIGKILL."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.send_signal(signal.SIGINT)
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
