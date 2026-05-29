"""ScreenBase + PlaceholderScreen — common screen contract."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from yt_uniquifier.gui.state import AppState


class ScreenBase(QWidget):
    """Base class for all sidebar-registered screens.

    All real screens inherit and take an AppState in the constructor.
    Override `on_show()` if the screen needs to refresh state when the
    user navigates to it (called by MainWindow on tab switch).
    """

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def on_show(self) -> None:  # pragma: no cover - default no-op
        """Hook for screens that need to refresh on navigation. Override."""


class PlaceholderScreen(QWidget):
    """Stub shown for screens not yet implemented in this release."""

    def __init__(self, name: str, lands_in: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(f"<h2>{name}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        sub = QLabel(f"Coming in {lands_in}.")
        sub.setObjectName("status")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)
