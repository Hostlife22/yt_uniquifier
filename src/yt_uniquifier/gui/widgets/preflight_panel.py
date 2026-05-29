"""PreflightPanel — colored list of preflight findings."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.core.preflight import PreflightFinding

_SEVERITY_STYLE = {
    "fail":   "background: #a83b3b; color: white;",
    "warn":   "background: #d1a93b; color: #16161e;",
    "ok":     "background: #3ba85c; color: white;",
}


class PreflightPanel(QWidget):
    """Coloured list of preflight findings + emits has_fail(bool).

    Hidden when there are no findings. Shows a compact one-line per
    finding: severity-tinted code + message + suggestion (if any).
    """

    has_fail = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)

    def set_findings(self, findings: list[PreflightFinding]) -> None:
        # Clear existing rows.
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not findings:
            self.hide()
            self.has_fail.emit(False)
            return

        self.show()
        any_fail = False
        for f in findings:
            row = self._row_for(f)
            self._layout.addWidget(row)
            if f.severity == "fail":
                any_fail = True
        self.has_fail.emit(any_fail)

    def _row_for(self, f: PreflightFinding) -> QFrame:
        frame = QFrame()
        h = QHBoxLayout(frame)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        badge = QLabel(f.severity.upper())
        badge.setFixedWidth(56)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "padding: 2px 6px; border-radius: 3px; font-weight: 600; "
            + _SEVERITY_STYLE.get(f.severity, ""),
        )
        h.addWidget(badge)

        text = QLabel(f"<b>{f.code}</b> — {f.message}")
        text.setWordWrap(True)
        h.addWidget(text, stretch=1)

        if f.suggestion:
            tip = QLabel(f"→ {f.suggestion}")
            tip.setObjectName("status")
            tip.setWordWrap(True)
            h.addWidget(tip, stretch=1)

        return frame
