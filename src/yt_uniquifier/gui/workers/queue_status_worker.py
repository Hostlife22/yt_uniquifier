"""Polls FileQueue.stats() periodically and emits stats dict."""

from __future__ import annotations

from pathlib import Path

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
            # cancel_token.wait blocks on the underlying threading.Event,
            # so cancel wakes the thread immediately — no 100 ms wakeup
            # latency, no 10 Hz spin. Returns True if cancelled within
            # the timeout, in which case we exit promptly.
            if self.cancel_token.wait(self.poll_sec):
                return
