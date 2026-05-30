"""Corpus — list / add / remove indexed fingerprints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.corpus_list_worker import CorpusListWorker
from yt_uniquifier.gui.workers.corpus_worker import CorpusWorker


class CorpusScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.corpus = Corpus()
        self.worker: CorpusWorker | None = None
        self.list_worker: CorpusListWorker | None = None
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Corpus")
        title.setObjectName("title")
        layout.addWidget(title)

        # Root info
        header = QHBoxLayout()
        header.addWidget(QLabel("Corpus root:"))
        root_lbl = QLabel(str(self.corpus.root))
        root_lbl.setObjectName("path")
        header.addWidget(root_lbl, stretch=1)
        layout.addLayout(header)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Path", "Added", "Samples", "Audio FP"],
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        header2 = self.table.horizontalHeader()
        if header2 is not None:
            header2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=1)

        # Actions
        controls = QHBoxLayout()
        self.add_btn = QPushButton("Add file…")
        self.add_btn.clicked.connect(self._on_add)
        controls.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self._on_remove)
        controls.addWidget(self.remove_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        controls.addWidget(self.refresh_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def _refresh(self) -> None:
        """Trigger a background corpus read.

        For a few-entry index the JSON parse is sub-millisecond, but on
        a corpus that has grown to hundreds of entries (especially after
        bulk add) the synchronous read briefly blocked the event loop.
        Dispatch a worker; render on its `listed` signal.
        """
        if self.list_worker is not None:
            return  # one in flight already
        self.status_label.setText("loading…")
        self.list_worker = CorpusListWorker(self.corpus)
        self.list_worker.listed.connect(self._on_listed)
        self.list_worker.failed.connect(self._on_list_failed)
        self.list_worker.start()

    def _on_listed(self, entries: object) -> None:
        if not isinstance(entries, list):
            entries = []
        self.table.setRowCount(0)
        for e in entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(e.id))
            self.table.setItem(r, 1, QTableWidgetItem(str(e.path)))
            ts = datetime.fromtimestamp(e.added_at).isoformat(timespec="seconds")
            self.table.setItem(r, 2, QTableWidgetItem(ts))
            self.table.setItem(r, 3, QTableWidgetItem(str(e.sample_count)))
            has_audio = "yes" if e.audio_fingerprint else "no"
            self.table.setItem(r, 4, QTableWidgetItem(has_audio))
        self.status_label.setText(f"{len(entries)} entries")
        self._drop_list_worker()

    def _on_list_failed(self, message: str) -> None:
        self.status_label.setText(f"refresh failed: {message}")
        self._drop_list_worker()

    def _drop_list_worker(self) -> None:
        """Join the worker thread before dropping the Python reference.

        Without quit()+wait() the QThread may still be alive in C++ when
        Python's last ref disappears, segfaulting the process.
        """
        if self.list_worker is None:
            return
        self.list_worker.quit()
        self.list_worker.wait(1000)
        self.list_worker = None

    def _on_add(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Add to corpus", "",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
        )
        if not path_str:
            return
        self.worker = CorpusWorker(self.corpus, Path(path_str))
        self.worker.entry_added.connect(lambda _e: self._refresh())
        self.worker.failed.connect(self._on_failed)
        self.worker.finished_ok.connect(lambda _r: self._on_finished())
        self.add_btn.setEnabled(False)
        self.status_label.setText(f"indexing {Path(path_str).name}…")
        self.worker.start()

    def _on_remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        id_item = self.table.item(row, 0)
        if id_item is None:
            return
        entry_id = id_item.text()
        if QMessageBox.question(
            self, "Remove entry",
            f"Remove corpus entry {entry_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.corpus.remove(entry_id)
        self._refresh()

    def _on_failed(self, msg: str) -> None:
        self.add_btn.setEnabled(True)
        self.status_label.setText(f"FAILED: {msg}")
        QMessageBox.warning(self, "Corpus add failed", msg)
        self._drop_worker()

    def _on_finished(self) -> None:
        self.add_btn.setEnabled(True)
        self._drop_worker()

    def _drop_worker(self) -> None:
        """Join the worker QThread before dropping the Python ref.

        Mirrors `_drop_list_worker`. Without quit()+wait() the C++
        QThread can outlive the Python reference and segfault on its
        next event dispatch.
        """
        if self.worker is None:
            return
        self.worker.quit()
        self.worker.wait(1000)
        self.worker = None
