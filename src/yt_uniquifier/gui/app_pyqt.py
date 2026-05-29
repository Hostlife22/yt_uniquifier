"""MainWindow with sidebar navigation + QStackedWidget content area.

v0.5.0 — replaces the single-window v0.4 shell. Adds 10 sidebar entries
(only Run functional in this release; others land in v0.5.1–v0.5.4).
"""

from __future__ import annotations

import contextlib
import sys
from typing import cast

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.screens.batch import BatchScreen
from yt_uniquifier.gui.screens.calibrate import CalibrateScreen
from yt_uniquifier.gui.screens.corpus import CorpusScreen
from yt_uniquifier.gui.screens.history import HistoryScreen
from yt_uniquifier.gui.screens.profile_editor import ProfileEditorScreen
from yt_uniquifier.gui.screens.qa_viewer import QaViewerScreen
from yt_uniquifier.gui.screens.queue import QueueScreen
from yt_uniquifier.gui.screens.run import RunScreen
from yt_uniquifier.gui.screens.settings import SettingsScreen
from yt_uniquifier.gui.screens.validation import ValidationScreen
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.theme import ThemeName, qss_for

# (Label, factory) — factory takes AppState and returns the screen widget.
# Placeholders are anonymous factories that ignore state.
SIDEBAR_ITEMS: list[tuple[str, str]] = [
    ("Run",            "v0.5.0"),     # functional
    ("Batch",          "v0.5.1"),
    ("Calibrate",      "v0.5.1"),
    ("QA Viewer",      "v0.5.2"),
    ("Profile Editor", "v0.5.2"),
    ("History",        "v0.5.2"),
    ("Corpus",         "v0.5.4"),
    ("Queue",          "v0.5.3"),
    ("Validation",     "v0.5.3"),
    ("Settings",       "v0.5.4"),
]


def _build_screen(label: str, lands_in: str, state: AppState) -> QWidget:
    """Instantiate the real screen if available, else PlaceholderScreen."""
    if label == "Run":
        return RunScreen(state)
    if label == "Batch":
        return BatchScreen(state)
    if label == "Calibrate":
        return CalibrateScreen(state)
    if label == "QA Viewer":
        return QaViewerScreen(state)
    if label == "Profile Editor":
        return ProfileEditorScreen(state)
    if label == "History":
        return HistoryScreen(state)
    if label == "Queue":
        return QueueScreen(state)
    if label == "Validation":
        return ValidationScreen(state)
    if label == "Corpus":
        return CorpusScreen(state)
    if label == "Settings":
        return SettingsScreen(state)
    from yt_uniquifier.gui.screens.base import PlaceholderScreen
    return PlaceholderScreen(label, lands_in)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("yt-uniquifier")
        self.resize(1100, 720)

        self.state = AppState()
        self.setStyleSheet(qss_for(cast(ThemeName, self.state.theme)))
        self.state.theme_changed.connect(self._on_theme_changed)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        for label, _lands_in in SIDEBAR_ITEMS:
            QListWidgetItem(label, self.sidebar)
        layout.addWidget(self.sidebar)

        # Stacked content
        self.stack = QStackedWidget()
        for label, lands_in in SIDEBAR_ITEMS:
            screen = _build_screen(label, lands_in, self.state)
            self.stack.addWidget(screen)
        layout.addWidget(self.stack, stretch=1)

        self.sidebar.currentRowChanged.connect(self._on_nav)
        self.sidebar.setCurrentRow(0)

        self.setCentralWidget(root)
        bar = QStatusBar()
        self.setStatusBar(bar)
        bar.showMessage("Ready.")

    def _on_nav(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        widget = self.stack.currentWidget()
        if isinstance(widget, ScreenBase):
            widget.on_show()

    def _on_theme_changed(self, theme: str) -> None:
        self.setStyleSheet(qss_for(cast(ThemeName, theme)))

    def closeEvent(self, event: QCloseEvent | None) -> None:  # type: ignore[override]
        """Cancel and join every running worker before the window closes.

        Without this, QueueStatusWorker's infinite poll loop keeps the
        process alive after the window is gone, and an active encode
        can keep writing to its output file mid-corruption.
        """
        # Persist any in-memory AppState (recent files, etc.) — Settings
        # was the only call site before, so closing without visiting it
        # discarded changes.
        with contextlib.suppress(Exception):
            self.state.save()

        # Walk every screen, locate any QThread-derived attribute that is
        # still running, request cooperative cancel, then wait briefly.
        for i in range(self.stack.count()):
            screen = self.stack.widget(i)
            if screen is None:
                continue
            for attr_name in dir(screen):
                if attr_name.startswith("__"):
                    continue
                try:
                    obj = getattr(screen, attr_name)
                except Exception:  # noqa: BLE001 - defensive walker
                    continue
                if isinstance(obj, QThread) and obj.isRunning():
                    cancel = getattr(obj, "request_cancel", None)
                    if callable(cancel):
                        cancel()
                    obj.quit()
                    obj.wait(3000)  # cap per worker; total <= 3s × N
        if event is not None:
            event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()
