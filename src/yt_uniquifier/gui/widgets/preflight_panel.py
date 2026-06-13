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
from yt_uniquifier.gui.theme import tokens_for


def _badge_style(theme: str, severity: str) -> str:
    """Compose the inline stylesheet for a severity badge.

    Reads bg/fg pairs from the current theme tokens instead of
    hard-coding — prevents the dark-only colors from leaking after a
    `state.theme_changed` signal switches to the light theme.
    """
    tokens = tokens_for(theme)
    bg = tokens.get(f"badge_{severity}_bg", tokens["badge_warn_bg"])
    fg = tokens.get(f"badge_{severity}_fg", tokens["badge_warn_fg"])
    return (
        f"padding: 2px 6px; border-radius: 3px; font-weight: 600; "
        f"background: {bg}; color: {fg};"
    )


class PreflightPanel(QWidget):
    """Coloured list of preflight findings + emits has_fail(bool).

    Hidden when there are no findings. Shows a compact one-line per
    finding: severity-tinted code + message + suggestion (if any).

    Pass `state` to subscribe to `theme_changed` so the badges repaint
    on theme switch. Construction without `state` keeps the widget
    usable in tests and isolated contexts; the badges will use the
    dark theme until `set_theme()` is called explicitly.
    """

    has_fail = pyqtSignal(bool)

    def __init__(self, state: object | None = None) -> None:
        super().__init__()
        self._theme: str = "dark"
        self._findings: list[PreflightFinding] = []
        self._build_ui()
        self.hide()
        if state is not None:
            theme = getattr(state, "theme", None)
            if isinstance(theme, str):
                self._theme = theme
            sig = getattr(state, "theme_changed", None)
            if sig is not None:
                sig.connect(self.set_theme)

    def _build_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)

    def set_theme(self, theme: str) -> None:
        """Repaint badges with the new theme's tokens. Idempotent."""
        if theme == self._theme:
            return
        self._theme = theme
        if self._findings:
            self.set_findings(self._findings)

    def set_findings(self, findings: list[PreflightFinding]) -> None:
        self._findings = list(findings)
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
        badge.setStyleSheet(_badge_style(self._theme, f.severity))
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
