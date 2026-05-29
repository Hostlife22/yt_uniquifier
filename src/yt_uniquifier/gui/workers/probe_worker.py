"""Quick ffprobe wrapper for showing source metadata on file drop."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.probe import probe
from yt_uniquifier.gui.workers.base import WorkerBase


class ProbeWorker(WorkerBase):
    """Async ffprobe — emits SourceMeta on success.

    Used by the Run screen to display source metadata immediately after
    the user drops a file, without blocking the UI thread.
    """

    probed = pyqtSignal(object)              # SourceMeta

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        try:
            meta = probe(self.path)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.probed.emit(meta)
        self.finished_ok.emit(meta)
