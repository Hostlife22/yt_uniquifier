"""FilePickerRow — single-line widget for picking input/output paths."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.state import AppState


class FilePickerRow(QWidget):
    """Label + path display + Browse button + drag-drop target.

    Emits `path_changed(Path)` whenever the user picks or drops a file.
    """

    path_changed = pyqtSignal(object)        # Path | None

    def __init__(
        self,
        label: str,
        kind: Literal["input", "output"] = "input",
        file_filter: str = "Video files (*.mp4 *.mov *.mkv *.webm);;All files (*)",
        state: AppState | None = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.file_filter = file_filter
        self.state = state
        self._path: Path | None = None
        self.setAcceptDrops(True)
        self._build_ui(label)

    def _build_ui(self, label: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        self.path_label = QLabel("(not selected)")
        self.path_label.setObjectName("path")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        layout.addWidget(self.path_label, stretch=1)
        self.browse_btn = QPushButton("&Browse…")
        self.browse_btn.clicked.connect(self._on_browse)
        # Strip the trailing colon and trim so the accessible name is
        # spoken as "Browse input file" rather than "Browse Input:".
        clean_label = label.rstrip(":").strip() or self.kind
        mark(
            self.browse_btn,
            f"Browse {self.kind} file",
            f"Pick a {clean_label.lower()} via the file dialog.",
        )
        layout.addWidget(self.browse_btn)

    def set_path(self, path: Path | None) -> None:
        """Programmatic path setter (does not re-emit if unchanged)."""
        if path == self._path:
            return
        self._path = path
        self.path_label.setText(str(path) if path else "(not selected)")
        self.path_changed.emit(path)

    def current_path(self) -> Path | None:
        return self._path

    def _on_browse(self) -> None:
        if self.kind == "output":
            path_str, _ = QFileDialog.getSaveFileName(
                self, "Output file", "", self.file_filter,
            )
        else:
            path_str, _ = QFileDialog.getOpenFileName(
                self, "Input file", "", self.file_filter,
            )
        if not path_str:
            return
        self.set_path(Path(path_str))

    # ---- drag-drop ----
    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is None:
            return
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        md = event.mimeData()
        if md is None:
            return
        urls = md.urls()
        if not urls:
            return
        local = urls[0].toLocalFile()
        if local:
            self.set_path(Path(local))
            event.acceptProposedAction()
