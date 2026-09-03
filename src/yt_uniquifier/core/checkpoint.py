"""Persist segment progress between runs.

state.json layout:
  {
    "schema_version": 1,
    "tool_version": "...",
    "input_md5": "...",
    "plan_hash": "...",
    "loudnorm_measurement": {... | null},
    "main_audio_path": "<absolute path | null>",
    "segments": [{"idx":0, "start_sec":..., "end_sec":..., "status":...,
                  "src_path":..., "out_path":...}, ...]
  }

All writes are atomic (write tmp, fsync, os.replace).
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement

SCHEMA_VERSION = 1
LOCK_FILENAME = ".lock.json"
CROSS_HOST_LOCK_LEASE_SEC = 24 * 3600

# B4 (v0.6.0): debounced flush thresholds. ``mark()`` writes to state
# but only forces a disk fsync when one of these is exceeded. The
# trade-off: up to ``DEBOUNCE_MAX_MARKS`` segments may revert from
# ``done`` → ``pending`` on a crash within the debounce window, which
# only costs a re-encode (resume invariant is preserved). On a
# 1000-segment plan with 4 workers this drops ~3990 fsync calls
# (1-4 s on SSD, 10-40 s on HDD) to ~100.
DEBOUNCE_MAX_MARKS = 10
DEBOUNCE_MAX_SEC = 0.25

_log = logging.getLogger(__name__)


def _segment_topology_matches(
    stored: object, fresh: list[Segment],
) -> bool:
    """Return whether cached and newly planned segment boundaries are identical."""
    if not isinstance(stored, list) or len(stored) != len(fresh):
        return False
    for raw, expected in zip(stored, fresh, strict=True):
        if not isinstance(raw, dict):
            return False
        try:
            idx = int(raw["idx"])
            start = float(raw["start_sec"])
            end = float(raw["end_sec"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            idx != expected.idx
            or abs(start - expected.start_sec) > 1e-9
            or abs(end - expected.end_sec) > 1e-9
        ):
            return False
    return True


def _pid_alive(pid: int) -> bool:
    """Cross-platform live-PID probe (POSIX + Windows).

    POSIX: ``os.kill(pid, 0)`` delivers no signal but raises
    ``ProcessLookupError`` for dead PIDs and ``PermissionError`` for a
    live foreign-owned PID.

    Windows: ``os.kill(pid, 0)`` is unsafe — it calls ``OpenProcess``
    with ``PROCESS_ALL_ACCESS`` and then ``TerminateProcess(handle, 0)``,
    which would actually try to kill the process. We use a ctypes
    ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``GetExitCodeProcess``
    probe instead: handle == 0 means the PID doesn't exist or we lack
    even query rights (the latter is treated as alive — conservative),
    STILL_ACTIVE (259) means running, any other exit code means reaped.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Conservative: don't steal the lock when uncertain.
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows-specific safe liveness probe via OpenProcess + GetExitCodeProcess.

    Lazy-imports ctypes so the POSIX path stays import-fast. Differentiates
    "PID doesn't exist" (ERROR_INVALID_PARAMETER on OpenProcess) from
    "access denied to a live foreign PID" (ERROR_ACCESS_DENIED).
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    # ``ctypes.WinDLL`` is Windows-only and not in the cross-platform
    # stubs; this branch only runs under win32 so it's safe at runtime.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ``ctypes.get_last_error`` is Windows-only and not in the
        # cross-platform stubs; the call only runs under win32 so
        # it's safe at runtime.
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        # Live foreign-owned PIDs land here on a non-admin token —
        # be conservative and treat as alive.
        return bool(err == ERROR_ACCESS_DENIED)
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return True  # conservative
        return bool(exit_code.value == STILL_ACTIVE)
    finally:
        kernel32.CloseHandle(handle)


class CheckpointStore:
    """Atomic, plan_hash-keyed segment progress store.

    Thread-safe: all public mutators and ``_flush`` acquire ``_lock`` so
    concurrent ``on_segment_done`` callbacks from ThreadPoolExecutor workers
    cannot race on ``self._state`` or on the tmp-file rename.
    """

    def __init__(self, work_dir: Path, plan: Plan) -> None:
        self.work_dir = work_dir
        self.plan = plan
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = work_dir / "state.json"
        self.lock_path = work_dir / LOCK_FILENAME
        self._state: dict[str, Any] = {}
        self._lock = threading.RLock()
        # B4 (v0.6.0): debounced flush counters. ``_flush_maybe`` honours
        # both — flush when EITHER N marks accumulated OR T seconds
        # elapsed since the last flush. Reset on every real flush.
        self._marks_since_flush = 0
        self._last_flush_at = time.monotonic()
        # A4 (v0.5.5): cross-process work_dir lock. Two `yt-uniq batch`
        # processes that accidentally share a work_dir would otherwise
        # race on the read-modify-write of state.json — the PID-suffixed
        # tmp prevents torn writes but not last-writer-wins on file
        # content. We acquire a lockfile keyed by (PID, hostname) and
        # raise if another live process already owns it.
        self._owns_lock = False
        self._acquire_process_lock()
        # Register cleanup so the lock is released on normal exit even
        # if the caller forgets to call .close().
        atexit.register(self._release_process_lock)

    # ---- lifecycle ----

    def init_or_resume(self, fresh_segments: list[Segment]) -> list[Segment]:
        """Either resume an existing state file or initialize a new one.

        v1.0.1: when resuming, each segment marked ``done`` is verified
        against the on-disk file at ``out_path``:
          * missing file → demoted to ``pending``;
          * zero-byte file → demoted to ``pending`` (and the file is
            left in place for operator inspection);
          * stored ``sha256`` present and disagrees with the recomputed
            digest → demoted to ``pending``;
          * stored ``sha256`` ``None`` (pre-v1.0.1 state files) → kept
            as ``done``; the next successful re-encode will populate it.
        Demotion mutates ``self._state`` and triggers a flush so the
        next resume cycle starts from a clean baseline.
        """
        with self._lock:
            if self.state_path.exists():
                try:
                    raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise CheckpointError(
                        f"state.json is unreadable; delete it or use a different "
                        f"--work-dir. ({exc})"
                    ) from exc
                same_plan = raw.get("plan_hash") == self.plan.plan_hash
                same_topology = _segment_topology_matches(
                    raw.get("segments"), fresh_segments,
                )
                if same_plan and same_topology:
                    self._state = raw
                    demoted_any = self._verify_done_segments_on_disk()
                    if demoted_any:
                        self._flush()
                    return [Segment.model_validate(s) for s in raw.get("segments", [])]
                # Plan or segment topology changed → invalidate by renaming the
                # stale file aside. Segment layout is a run option rather than
                # part of Plan, so changing --segment-sec can keep plan_hash
                # stable while making every cached seg_NNNN.mkv incompatible.
                # Nanoseconds avoid clobbering multiple invalidations in one
                # second while preserving the existing stale-* operator glob.
                stale = self.state_path.with_suffix(
                    f".json.stale-{time.time_ns()}"
                )
                os.replace(self.state_path, stale)

            self._state = {
                "schema_version": SCHEMA_VERSION,
                "tool_version": _tool_version(),
                "plan_hash": self.plan.plan_hash,
                "run_seed": self.plan.run_seed,
                "loudnorm_measurement": None,
                "main_audio_path": None,
                "main_audio_sha256": None,
                "output_path": None,
                "output_sha256": None,
                "segments": [s.model_dump(mode="json") for s in fresh_segments],
            }
            self._flush()
            return list(fresh_segments)

    def stored_run_seed(self) -> int | None:
        """Return the run_seed persisted in state.json, if any."""
        with self._lock:
            raw = self._state.get("run_seed")
            return int(raw) if raw is not None else None

    # ---- segment ops ----

    def mark(
        self,
        idx: int,
        status: Literal["pending", "in_progress", "done", "failed"],
        *,
        src_path: Path | None = None,
        out_path: Path | None = None,
        sha256: str | None = None,
    ) -> None:
        with self._lock:
            segs = self._state.get("segments", [])
            if not 0 <= idx < len(segs):
                raise CheckpointError(f"segment idx {idx} out of range")
            s = segs[idx]
            s["status"] = status
            if src_path is not None:
                s["src_path"] = str(src_path)
            if out_path is not None:
                s["out_path"] = str(out_path)
            # v1.0.1: persist the encoded segment's sha256 alongside the
            # status. On resume, init_or_resume re-hashes the on-disk
            # file and demotes the segment if the digest disagrees. A
            # transition to ``pending`` clears the stale hash so the
            # re-encode isn't compared against a previous attempt.
            if sha256 is not None:
                s["sha256"] = sha256
            elif status == "pending":
                s["sha256"] = None
            # B4 (v0.6.0): debounce — most callers (the parallel segment
            # workers) hit this hot path; coalesce fsyncs over a short
            # window. terminal statuses (done/failed) get the full mark
            # accounting; pending / in_progress transitions are also
            # safely debounced because resume just re-marks them.
            self._marks_since_flush += 1
            self._flush_maybe()

    def flush(self) -> None:
        """Force-flush any pending state changes. Public API for the
        orchestrator to call at phase boundaries (segment loop done,
        main_audio done, before concat).
        """
        with self._lock:
            self._flush()

    def all_segments(self) -> list[Segment]:
        with self._lock:
            return [Segment.model_validate(s) for s in self._state.get("segments", [])]

    def pending(self) -> list[Segment]:
        # Filter at the dict level before model_validate to avoid parsing
        # every segment twice (once for all_segments, once for the
        # filter). On thousand-segment plans this halves the cost.
        with self._lock:
            return [
                Segment.model_validate(s)
                for s in self._state.get("segments", [])
                if s.get("status") != "done"
            ]

    def all_done(self) -> bool:
        with self._lock:
            return all(
                s.get("status") == "done"
                for s in self._state.get("segments", [])
            )

    # ---- loudnorm cache ----

    def get_loudnorm(self) -> LoudnormMeasurement | None:
        with self._lock:
            raw = self._state.get("loudnorm_measurement")
            return LoudnormMeasurement.model_validate(raw) if raw else None

    def set_loudnorm(self, m: LoudnormMeasurement) -> None:
        with self._lock:
            self._state["loudnorm_measurement"] = m.model_dump(mode="json")
            # Phase-boundary write: force-flush instead of debouncing.
            self._flush()

    # ---- pause marker (v0.7 R6 / F5) ----

    def set_paused_at(self, ts_wall: float | None) -> None:
        """Record / clear the wall-clock pause timestamp.

        Persisted as ISO-8601 in ``state.json``: a crash inside the
        pause window leaves a visible artefact ("paused_at": "...") so
        the next resume can warn the user that the previous run was
        interrupted mid-pause and the subprocess was lost.
        Clearing (``None``) writes ``None`` — caller must do so on
        resume and on terminal phases (done / cancelled / failed).
        """
        with self._lock:
            if ts_wall is None:
                self._state["paused_at"] = None
            else:
                import datetime as _dt
                self._state["paused_at"] = _dt.datetime.fromtimestamp(
                    ts_wall, tz=_dt.UTC,
                ).isoformat()
            # Phase-boundary write: pause is a user-visible event,
            # never debounce.
            self._flush()

    def get_paused_at(self) -> str | None:
        with self._lock:
            raw = self._state.get("paused_at")
            return str(raw) if raw else None

    # ---- main audio cache ----

    def get_main_audio(self) -> Path | None:
        with self._lock:
            raw = self._state.get("main_audio_path")
            if not raw:
                return None
            path = Path(raw)
            if not _artifact_matches(path, self._state.get("main_audio_sha256")):
                self._state["main_audio_path"] = None
                self._state["main_audio_sha256"] = None
                self._flush()
                return None
            return path

    def set_main_audio(self, path: Path) -> None:
        with self._lock:
            self._state["main_audio_path"] = str(path)
            self._state["main_audio_sha256"] = sha256_file(path)
            # Phase-boundary write: force-flush instead of debouncing.
            self._flush()

    def output_is_valid(self, path: Path) -> bool:
        """Return whether *path* is the exact finalized artifact we recorded."""
        with self._lock:
            stored_path = self._state.get("output_path")
            stored_hash = self._state.get("output_sha256")
            if not stored_path or Path(stored_path).resolve(strict=False) != path.resolve(
                strict=False
            ):
                return False
            return _artifact_matches(path, stored_hash, require_hash=True)

    def set_output(self, path: Path) -> None:
        """Persist the finalized output identity for safe completed-run no-op."""
        with self._lock:
            self._state["output_path"] = str(path)
            self._state["output_sha256"] = sha256_file(path)
            self._flush()

    # ---- internals ----

    # ---- A4 cross-process lock ----

    def _acquire_process_lock(self) -> None:
        """Take ownership of the work_dir for this process.

        Raises ``CheckpointError`` if another live process already owns
        the lock; transparently reclaims an orphaned lock (owner PID is
        dead) and logs a warning so operators see why their work_dir
        was suddenly stolen.
        """
        my_pid = os.getpid()
        my_host = socket.gethostname()
        while self.lock_path.exists():
            try:
                raw = json.loads(self.lock_path.read_text(encoding="utf-8"))
                owner_pid = int(raw.get("pid", 0))
                owner_host = str(raw.get("hostname", ""))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, json.JSONDecodeError):
                # A competing O_EXCL creator may still be writing. Never
                # unlink a partially-written lock underneath it. The creator
                # writes immediately after O_EXCL, so a few short retries close
                # that visibility window without weakening stale recovery.
                recovered = None
                for _ in range(3):
                    time.sleep(0.01)
                    try:
                        recovered = json.loads(
                            self.lock_path.read_text(encoding="utf-8")
                        )
                        break
                    except FileNotFoundError:
                        break
                    except (OSError, ValueError, json.JSONDecodeError):
                        continue
                if recovered is not None:
                    raw = recovered
                    owner_pid = int(recovered.get("pid", 0))
                    owner_host = str(recovered.get("hostname", ""))
                else:
                    owner_pid, owner_host = 0, ""

            same_host = owner_host == my_host
            same_pid = owner_pid == my_pid
            if same_pid and same_host:
                # Re-entry from the same process (e.g. resumed run, test
                # fixture re-creating the store) — fine, keep the lock.
                self._owns_lock = True
                return
            if same_host and _pid_alive(owner_pid):
                raise CheckpointError(
                    f"work_dir {self.work_dir} is already in use by "
                    f"PID {owner_pid} on {owner_host}. Two processes "
                    "cannot share a work_dir for the same plan — last-"
                    "writer-wins on state.json would silently lose "
                    "segment progress. Use a distinct --work-dir per "
                    "input (the typical pattern is work_dir/<plan_hash>).",
                )
            if owner_host and not same_host:
                acquired_at = float(raw.get("acquired_at", 0.0))
                if time.time() - acquired_at < CROSS_HOST_LOCK_LEASE_SEC:
                    raise CheckpointError(
                        f"work_dir {self.work_dir} is locked by PID {owner_pid} "
                        f"on another host ({owner_host}); refusing an unsafe reclaim"
                    )
            _log.warning(
                "checkpoint: reclaiming stale lock at %s "
                "(prev owner: pid=%s host=%s)",
                self.lock_path, owner_pid, owner_host,
            )
            self.lock_path.unlink(missing_ok=True)

        # O_EXCL makes ownership acquisition atomic across processes.
        payload = json.dumps({
            "pid": my_pid,
            "hostname": my_host,
            "plan_hash": self.plan.plan_hash,
            "acquired_at": time.time(),
        })
        try:
            fd = os.open(
                self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            )
        except FileExistsError:
            return self._acquire_process_lock()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            self.lock_path.unlink(missing_ok=True)
            raise
        self._owns_lock = True

    def _release_process_lock(self) -> None:
        """Best-effort lock release.

        Idempotent — atexit may fire after an explicit close(); the
        ``_owns_lock`` flag guards against double-release. We only
        unlink the lock if we still own it AND no one else has stolen
        it in the meantime.
        """
        if not self._owns_lock:
            return
        self._owns_lock = False
        try:
            if not self.lock_path.exists():
                return
            raw = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if int(raw.get("pid", 0)) == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            # If we cannot parse / stat the lock, leave it alone; a
            # future process will reclaim it via the dead-PID path.
            pass

    def close(self) -> None:
        """Release the cross-process lock and flush any pending state.

        ``atexit`` will eventually release the lock anyway, but tests
        and long-running daemons that create many CheckpointStore
        instances should call this to free the lock as soon as work
        completes. We also force-flush any debounced state changes so
        the post-close ``state.json`` reflects every recorded mark.
        """
        with self._lock:
            if self._marks_since_flush > 0:
                # B4: ensure no debounced marks are dropped on close.
                self._flush()
        self._release_process_lock()

    def __enter__(self) -> CheckpointStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals ----

    def _verify_done_segments_on_disk(self) -> bool:
        """v1.0.1: re-verify every ``done`` segment against its output file.

        Returns True if any segment was demoted to ``pending``. Caller
        must hold ``self._lock`` and is responsible for flushing.
        """
        segs = self._state.get("segments", [])
        demoted = False
        for s in segs:
            if s.get("status") != "done":
                continue
            out_path_raw = s.get("out_path")
            if not out_path_raw:
                # Resume entry without an out_path is suspicious — demote.
                _log.warning(
                    "checkpoint: segment %s marked done but missing out_path; "
                    "demoting to pending",
                    s.get("idx"),
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
                continue
            out_path = Path(out_path_raw)
            if not out_path.exists():
                _log.warning(
                    "checkpoint: segment %s out_path %s vanished; demoting "
                    "to pending",
                    s.get("idx"), out_path,
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
                continue
            try:
                size = out_path.stat().st_size
            except OSError as exc:
                _log.warning(
                    "checkpoint: segment %s stat failed (%s); demoting",
                    s.get("idx"), exc,
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
                continue
            if size == 0:
                _log.warning(
                    "checkpoint: segment %s out_path %s is zero bytes; "
                    "demoting to pending",
                    s.get("idx"), out_path,
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
                continue
            stored = s.get("sha256")
            if not stored:
                # Pre-v1.0.1 state file — accept the done status and let
                # the next successful re-encode (if any) populate sha256.
                continue
            try:
                actual = sha256_file(out_path)
            except OSError as exc:
                _log.warning(
                    "checkpoint: segment %s sha256 read failed (%s); demoting",
                    s.get("idx"), exc,
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
                continue
            if actual != stored:
                _log.warning(
                    "checkpoint: segment %s sha256 mismatch "
                    "(stored=%s, actual=%s); demoting to pending",
                    s.get("idx"), stored[:12], actual[:12],
                )
                s["status"] = "pending"
                s["sha256"] = None
                demoted = True
        return demoted

    def _flush_maybe(self) -> None:
        """B4 (v0.6.0): flush only when debounce thresholds are exceeded.

        Caller must hold ``self._lock``. Honours both the count
        (``DEBOUNCE_MAX_MARKS``) and the time (``DEBOUNCE_MAX_SEC``)
        thresholds — whichever trips first forces a real fsync.
        Resume safety: a crash within the window means up to
        ``DEBOUNCE_MAX_MARKS`` segments revert to ``pending`` on the
        next run, which costs a re-encode but never produces a torn
        ``state.json`` (atomic-replace contract is unchanged).
        """
        if self._marks_since_flush >= DEBOUNCE_MAX_MARKS:
            self._flush()
            return
        if time.monotonic() - self._last_flush_at >= DEBOUNCE_MAX_SEC:
            self._flush()

    def _flush(self) -> None:
        # Lock is RLock; reentrant when called from a public method that
        # already holds it. Public entry points (init_or_resume, mark,
        # set_loudnorm, set_main_audio) wrap state mutations + flush
        # together so they are atomic w.r.t. concurrent workers.
        with self._lock:
            # PID + random suffix keeps temporary files distinct even when
            # multiple store instances are re-entered from the same process.
            tmp = self.state_path.with_suffix(
                f".json.{os.getpid()}.{secrets.token_hex(4)}.tmp"
            )
            payload = json.dumps(self._state, indent=2, default=str)
            # write → flush → fsync the *tmp* file before rename. Without
            # fsync the OS may have queued the page write but not committed
            # it; a crash between os.replace and the page hitting disk
            # produces a zero-byte state.json — the exact failure the
            # atomic-write pattern is supposed to prevent.
            #
            # Open with mode 0o600 so the file is owner-only. state.json
            # carries absolute paths to the user's source / output / main
            # audio files — on shared / mis-umask'd hosts this would
            # otherwise leak which content a user is processing.
            try:
                fd = os.open(
                    tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.state_path)
            finally:
                # ENOSPC, permission errors, and failed replaces must not
                # accumulate partial checkpoint files. Never mask the primary
                # persistence error if cleanup itself is refused.
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
            # B4 (v0.6.0): reset debounce accounting after the real
            # write. Callers that bypass _flush_maybe (set_loudnorm,
            # set_main_audio, explicit flush()) still drop the counter.
            self._marks_since_flush = 0
            self._last_flush_at = time.monotonic()


def _tool_version() -> str:
    from yt_uniquifier import __version__
    return __version__


# v1.0.1: 1 MiB streaming SHA-256. Keeping the chunk size here so the
# checkpoint verifier and the orchestrator's post-segment hash use the
# same constant — drift would not corrupt anything (sha256 is content-
# only), but it would muddy benchmarking numbers.
_SHA256_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Streamed SHA-256 of a file. 1 MiB chunks, hex digest.

    Used by ``CheckpointStore._verify_done_segments_on_disk`` on resume
    and by the orchestrator after each successful segment encode so the
    two sides agree on the digest of every ``done`` segment.
    """
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_SHA256_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _artifact_matches(
    path: Path,
    stored_hash: object,
    *,
    require_hash: bool = False,
) -> bool:
    """Validate a cached artifact without allowing zero-byte placeholders."""
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if not stored_hash:
            return not require_hash
        return sha256_file(path) == str(stored_hash)
    except OSError:
        return False
