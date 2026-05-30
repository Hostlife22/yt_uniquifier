"""Background reader for ``Corpus.list_all()``.

For a small corpus the JSON parse is cheap. For users with hundreds
or thousands of indexed entries the synchronous read briefly blocks
paintEvent, especially right after the entry_added signal fires
(triggering an immediate re-read on the GUI thread).
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.gui.workers.base import WorkerBase


class CorpusListWorker(WorkerBase):
    """Async wrapper around ``Corpus.list_all()``.

    Emits:
        listed(list[CorpusEntry]): full entry list on success.
        failed(str):               error message on JSON parse failure.
    """

    listed = pyqtSignal(object)  # list[CorpusEntry]

    def __init__(self, corpus: Corpus) -> None:
        super().__init__()
        self.corpus = corpus

    def run(self) -> None:
        try:
            entries = self.corpus.list_all()
        except Exception as exc:  # noqa: BLE001 - top-level worker handler
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.listed.emit(entries)
        # Emit a lightweight summary on the base `finished_ok` channel
        # (entry count, not the full list) so downstream code reacts to
        # completion without re-serialising the entries list through
        # Qt's signal mechanism.
        self.finished_ok.emit(len(entries))
