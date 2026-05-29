"""LogConsole — monospace log viewer with copy / filter / cap."""

from __future__ import annotations

from typing import Literal

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

LogLevel = Literal["info", "progress", "log", "error"]

# Subset of common color names; specific palette tuned per theme.
_LEVEL_COLOR = {
    "info":     "#c8c8d0",
    "progress": "#3b6ea8",
    "log":      "#9b9ba8",
    "error":    "#c44f4f",
}


class LogConsole(QWidget):
    """Appendable log with level filter and copy-to-clipboard."""

    def __init__(self, max_lines: int = 2000) -> None:
        super().__init__()
        self.max_lines = max_lines
        self._lines: list[tuple[LogLevel, str]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toolbar
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["all", "info", "progress", "log", "error"])
        self.filter_combo.currentTextChanged.connect(lambda _: self._refresh())
        bar.addWidget(self.filter_combo)
        bar.addStretch(1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy_all)
        bar.addWidget(self.copy_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)
        layout.addLayout(bar)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, stretch=1)

    def log(self, line: str, level: LogLevel = "info") -> None:
        self._lines.append((level, line))
        # Cap retained lines (FIFO).
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]
        # Append-only refresh if filter allows; else just don't show.
        if self._allows(level):
            self._append_html(level, line)

    def clear(self) -> None:
        self._lines = []
        self.text.clear()

    def line_count(self) -> int:
        return len(self._lines)

    def _allows(self, level: LogLevel) -> bool:
        f = self.filter_combo.currentText()
        return f == "all" or f == level

    def _refresh(self) -> None:
        self.text.clear()
        for level, line in self._lines:
            if self._allows(level):
                self._append_html(level, line)

    def _append_html(self, level: LogLevel, line: str) -> None:
        color = _LEVEL_COLOR.get(level, "#c8c8d0")
        safe = (
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.text.append(f'<span style="color: {color}">{safe}</span>')

    def _copy_all(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText("\n".join(line for _, line in self._lines))
