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
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Literal

from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement

SCHEMA_VERSION = 1
LOCK_FILENAME = ".lock.json"

_log = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """Cross-platform live-PID probe (POSIX + Windows).

    ``os.kill(pid, 0)`` is the canonical liveness check on POSIX: it
    delivers no signal but raises ``ProcessLookupError`` for dead PIDs.
    On Windows the same call raises ``OSError`` with errno ESRCH for
    a dead PID and ``PermissionError`` for a live foreign-owned PID.
    """
    if pid <= 0:
        return False
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
        """Either resume an existing state file or initialize a new one."""
        with self._lock:
            if self.state_path.exists():
                try:
                    raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise CheckpointError(
                        f"state.json is unreadable; delete it or use a different "
                        f"--work-dir. ({exc})"
                    ) from exc
                if raw.get("plan_hash") == self.plan.plan_hash:
                    self._state = raw
                    return [Segment.model_validate(s) for s in raw.get("segments", [])]
                # Plan changed → invalidate by renaming the stale file aside.
                # Timestamp suffix preserves history if the user retries the
                # same work_dir with multiple profile variations.
                stale = self.state_path.with_suffix(f".json.stale-{int(time.time())}")
                os.replace(self.state_path, stale)

            self._state = {
                "schema_version": SCHEMA_VERSION,
                "tool_version": _tool_version(),
                "plan_hash": self.plan.plan_hash,
                "run_seed": self.plan.run_seed,
                "loudnorm_measurement": None,
                "main_audio_path": None,
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
            self._flush()

    # ---- main audio cache ----

    def get_main_audio(self) -> Path | None:
        with self._lock:
            raw = self._state.get("main_audio_path")
            return Path(raw) if raw else None

    def set_main_audio(self, path: Path) -> None:
        with self._lock:
            self._state["main_audio_path"] = str(path)
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
        if self.lock_path.exists():
            try:
                raw = json.loads(self.lock_path.read_text(encoding="utf-8"))
                owner_pid = int(raw.get("pid", 0))
                owner_host = str(raw.get("hostname", ""))
            except (OSError, ValueError, json.JSONDecodeError):
                # Corrupt lock — treat as orphaned and overwrite.
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
            # Either cross-host (we cannot safely verify liveness across
            # hosts via os.kill) or dead-PID-on-same-host. The
            # distributed batch (`yt-uniq worker`) coordinates via the
            # queue, not via shared work_dirs, so this should not happen
            # in practice — but we surface a warning so an operator
            # notices if it does.
            _log.warning(
                "checkpoint: reclaiming stale lock at %s "
                "(prev owner: pid=%s host=%s)",
                self.lock_path, owner_pid, owner_host,
            )

        # Atomic-write the new lock.
        tmp = self.lock_path.with_suffix(f".json.{my_pid}.tmp")
        payload = json.dumps({
            "pid": my_pid,
            "hostname": my_host,
            "plan_hash": self.plan.plan_hash,
            "acquired_at": time.time(),
        })
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.lock_path)
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
        """Release the cross-process lock explicitly.

        ``atexit`` will eventually release it anyway, but tests and
        long-running daemons that create many CheckpointStore instances
        should call this to free the lock as soon as work completes.
        """
        self._release_process_lock()

    def __enter__(self) -> "CheckpointStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals ----

    def _flush(self) -> None:
        # Lock is RLock; reentrant when called from a public method that
        # already holds it. Public entry points (init_or_resume, mark,
        # set_loudnorm, set_main_audio) wrap state mutations + flush
        # together so they are atomic w.r.t. concurrent workers.
        with self._lock:
            # PID-suffix the tmp file so concurrent `yt-uniq batch`
            # processes sharing a work_dir cannot stomp on each other's
            # partially-written tmp before os.replace lands. Matches the
            # convention used by encoder.py::_save_cache and the keyframe
            # cache in segmenter.py. The intra-process RLock prevents
            # racing within one worker; the PID prevents racing across
            # workers.
            tmp = self.state_path.with_suffix(f".json.{os.getpid()}.tmp")
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
            fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)


def _tool_version() -> str:
    from yt_uniquifier import __version__
    return __version__
