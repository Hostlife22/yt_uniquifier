"""History — past runs with re-open / re-run actions."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState, HistoryEntry


class HistoryScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._all_entries: list[HistoryEntry] = []
        self._build_ui()
        self.state.history_changed.connect(self._on_history_changed)
        self._on_history_changed(self.state.history)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("History")
        title.setObjectName("title")
        layout.addWidget(title)

        # Filter
        row = QHBoxLayout()
        row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filename / profile / encoder / status")
        self.filter_edit.textChanged.connect(self._refresh)
        mark(self.filter_edit, "History filter",
             "Substring filter applied to source, profile, encoder, and status columns.")
        row.addWidget(self.filter_edit)
        self.clear_btn = QPushButton("&Clear all")
        self.clear_btn.clicked.connect(self._clear_history)
        mark(self.clear_btn, "Clear history",
             "Remove every entry from the run history after confirmation.")
        row.addWidget(self.clear_btn)
        layout.addLayout(row)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Date", "Source", "Profile", "Encoder", "Status", "Output", "Actions",
        ])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

    def _on_history_changed(self, entries: list[HistoryEntry]) -> None:
        self._all_entries = list(entries)
        self._refresh()

    def _refresh(self) -> None:
        filter_text = self.filter_edit.text().lower().strip()
        self.table.setRowCount(0)
        for entry in self._all_entries:
            haystack = " ".join([
                entry.source_path, entry.profile_name,
                entry.encoder_name, entry.status,
            ]).lower()
            if filter_text and filter_text not in haystack:
                continue
            self._append_row(entry)

    def _append_row(self, entry: HistoryEntry) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(entry.timestamp))
        self.table.setItem(r, 1, QTableWidgetItem(Path(entry.source_path).name))
        self.table.setItem(r, 2, QTableWidgetItem(entry.profile_name))
        self.table.setItem(r, 3, QTableWidgetItem(entry.encoder_name))
        self.table.setItem(r, 4, QTableWidgetItem(entry.status))
        self.table.setItem(r, 5, QTableWidgetItem(entry.output_path))

        # Actions cell
        actions_widget = self._build_actions(entry)
        self.table.setCellWidget(r, 6, actions_widget)

    def _build_actions(self, entry: HistoryEntry) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)
        btn_out = QPushButton("Open output")
        btn_out.clicked.connect(
            lambda: self._open_path(entry.output_path),
        )
        source_name = Path(entry.source_path).name
        mark(btn_out, f"Open output for {source_name}",
             f"Open the output file produced from {source_name} in the system viewer.")
        h.addWidget(btn_out)
        if entry.qa_html_path:
            btn_qa = QPushButton("QA")
            btn_qa.clicked.connect(
                lambda: self._open_path(entry.qa_html_path),
            )
            mark(btn_qa, f"Open QA report for {source_name}",
                 f"Open the QA HTML report for {source_name} in the browser.")
            h.addWidget(btn_qa)
        return w

    def _open_path(self, path: str | None) -> None:
        if not path:
            return
        p = Path(path)
        if not p.exists():
            QMessageBox.warning(self, "Missing", f"{p} no longer exists.")
            return
        webbrowser.open(p.as_uri())

    def _clear_history(self) -> None:
        if QMessageBox.question(
            self, "Clear history",
            "Remove all history entries? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.state.clear_history()

    def on_show(self) -> None:
        """Refresh whenever the user navigates here (catches background updates)."""
        self._on_history_changed(self.state.history)
