"""CorpusWorker — index a file into the corpus (multi-second op)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.gui.workers.base import WorkerBase


class CorpusWorker(WorkerBase):
    """Add a file to the corpus; emits the new CorpusEntry on success."""

    entry_added = pyqtSignal(object)             # CorpusEntry

    def __init__(self, corpus: Corpus, path: Path, *, samples: int = 60) -> None:
        super().__init__()
        self.corpus = corpus
        self.path = path
        self.samples = samples

    def run(self) -> None:
        try:
            entry = self.corpus.add(self.path, samples=self.samples)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.entry_added.emit(entry)
        self.finished_ok.emit({"entry_id": entry.id, "path": str(entry.path)})
