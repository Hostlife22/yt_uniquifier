"""Off-thread wrapper for FileQueue filesystem operations.

`FileQueue.add` and `FileQueue.reap_stale` walk the queue directories
and rename files — synchronous I/O that on networked queue roots can
take seconds. Running it on the GUI thread froze the event loop while
users dragged in batches.

One operation per instance. The screen connects to
`done(int, str)` (count, summary) and `failed(str)`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.queue.leasing import FileQueue, init_queue
from yt_uniquifier.gui.workers.base import WorkerBase

Op = Literal["add", "reset_stale", "init"]


class QueueIoWorker(WorkerBase):
    """One queue filesystem operation per instance."""

    done = pyqtSignal(int, str)

    def __init__(
        self,
        queue_root: Path,
        op: Op,
        *,
        files: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self.queue_root = queue_root
        self.op = op
        self.files = files or []

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            if self.op == "init":
                init_queue(self.queue_root)
                self.done.emit(0, f"queue ready at {self.queue_root}")
                return
            q = FileQueue(self.queue_root)
            if self.op == "add":
                added = 0
                for f in self.files:
                    try:
                        q.add(Path(f))
                        added += 1
                    except FileExistsError:
                        continue
                self.done.emit(added, f"added {added} file(s)")
                return
            if self.op == "reset_stale":
                n = q.reap_stale()
                self.done.emit(n, f"reclaimed {n} stale file(s)")
                return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
