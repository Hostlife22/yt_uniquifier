"""Drain queue: lease → run_full → release_done/failed → repeat."""

from __future__ import annotations

import contextlib
from pathlib import Path
from time import sleep, time

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.queue.leasing import FileQueue
from yt_uniquifier.gui.workers.base import WorkerBase


class QueueWorker(WorkerBase):
    """Single-process queue drainer. Heartbeats while encoding."""

    lease_acquired = pyqtSignal(str)             # source path
    file_done = pyqtSignal(str, str)             # source path, output path
    file_failed = pyqtSignal(str, str)           # source path, error

    def __init__(
        self,
        root: Path,
        profile: Profile,
        out_dir: Path,
        *,
        encoder_override: str | None = None,
        workers: int = 1,
        work_dir_root: Path | None = None,
        poll_sec: float = 3.0,
        heartbeat_sec: float = 30.0,
        stop_after_empty: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.profile = profile
        self.out_dir = out_dir
        self.encoder_override = encoder_override
        self.workers = workers
        self.work_dir_root = work_dir_root or (
            Path.home() / ".cache" / "yt_uniquifier" / "worker"
        )
        self.poll_sec = poll_sec
        self.heartbeat_sec = heartbeat_sec
        self.stop_after_empty = stop_after_empty

    def run(self) -> None:
        try:
            q = FileQueue(self.root)
        except Exception as exc:
            self.failed.emit(f"queue not initialised: {exc}")
            return

        self.out_dir.mkdir(parents=True, exist_ok=True)
        last_heartbeat = time()

        while not self.cancel_token.is_cancelled():
            leased = q.lease()
            if leased is None:
                if self.stop_after_empty:
                    self.log.emit("queue empty; exiting")
                    break
                sleep(self.poll_sec)
                continue

            self.lease_acquired.emit(str(leased))
            out = self.out_dir / f"{leased.stem}.uniq.mp4"
            try:
                plan = build_plan(leased, self.profile, self.encoder_override)
                opts = RunOptions(
                    work_dir=self.work_dir_root / plan.plan_hash,
                    output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                    workers=self.workers,
                )
                run_full(plan, opts, cancel_token=self.cancel_token)
                q.release_done(leased)
                self.file_done.emit(str(leased), str(out))
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                with contextlib.suppress(Exception):
                    q.release_failed(leased, msg)
                self.file_failed.emit(str(leased), msg)

            if time() - last_heartbeat > self.heartbeat_sec:
                with contextlib.suppress(Exception):
                    q.heartbeat()
                last_heartbeat = time()

        reason = "cancelled" if self.cancel_token.is_cancelled() else "queue_empty"
        self.finished_ok.emit({"reason": reason})
