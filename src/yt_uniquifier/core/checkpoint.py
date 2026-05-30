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

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement

SCHEMA_VERSION = 1


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
        self._state: dict[str, Any] = {}
        self._lock = threading.RLock()

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
        raw = self._state.get("loudnorm_measurement")
        return LoudnormMeasurement.model_validate(raw) if raw else None

    def set_loudnorm(self, m: LoudnormMeasurement) -> None:
        with self._lock:
            self._state["loudnorm_measurement"] = m.model_dump(mode="json")
            self._flush()

    # ---- main audio cache ----

    def get_main_audio(self) -> Path | None:
        raw = self._state.get("main_audio_path")
        return Path(raw) if raw else None

    def set_main_audio(self, path: Path) -> None:
        with self._lock:
            self._state["main_audio_path"] = str(path)
            self._flush()

    # ---- internals ----

    def _flush(self) -> None:
        # Lock is RLock; reentrant when called from a public method that
        # already holds it. Public entry points (init_or_resume, mark,
        # set_loudnorm, set_main_audio) wrap state mutations + flush
        # together so they are atomic w.r.t. concurrent workers.
        with self._lock:
            tmp = self.state_path.with_suffix(".json.tmp")
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
