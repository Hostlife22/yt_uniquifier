"""Polls FileQueue.stats() periodically and emits stats dict."""

from __future__ import annotations

from pathlib import Path
from time import sleep

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.queue.leasing import FileQueue
from yt_uniquifier.gui.workers.base import WorkerBase


class QueueStatusWorker(WorkerBase):
    """Long-running poller — emits stats every `poll_sec` until cancelled."""

    stats = pyqtSignal(dict)              # {"pending": N, "in_progress": N, ...}

    def __init__(self, root: Path, *, poll_sec: float = 2.0) -> None:
        super().__init__()
        self.root = root
        self.poll_sec = poll_sec

    def run(self) -> None:
        if not (self.root / "pending").exists():
            self.failed.emit(
                f"queue not initialised at {self.root} "
                "(run `yt-uniq queue init` or use the Init button)",
            )
            return
        try:
            q = FileQueue(self.root)
        except Exception as exc:
            self.failed.emit(f"queue not initialised: {exc}")
            return

        while not self.cancel_token.is_cancelled():
            try:
                s = q.stats()
                self.stats.emit(dict(s))
            except Exception as exc:
                self.log.emit(f"stats error: {exc}")
            # Slice the sleep so cancel is responsive.
            slept = 0.0
            while slept < self.poll_sec and not self.cancel_token.is_cancelled():
                sleep(0.1)
                slept += 0.1
