"""ScreenBase + PlaceholderScreen — common screen contract."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QCloseEvent
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

    def shutdown_workers(self, wait_ms: int = 16_000) -> bool:
        """Cooperatively stop every worker owned by this screen.

        Screens are also instantiated directly by tests and embedders, so
        cleanup cannot live only in ``MainWindow.closeEvent``.  In particular,
        EncoderSelector owns a nested detection QThread that is not present in
        the screen's attribute dictionary.
        """
        from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector

        all_stopped = True
        for selector in self.findChildren(EncoderSelector):
            all_stopped = selector.shutdown_detection(wait_ms) and all_stopped

        for obj in tuple(vars(self).values()):
            if not isinstance(obj, QThread) or not obj.isRunning():
                continue
            cancel = getattr(obj, "request_cancel", None)
            if callable(cancel):
                cancel()
            obj.quit()
            all_stopped = obj.wait(wait_ms) and all_stopped
        return all_stopped

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Never let Qt destroy a screen while one of its QThreads runs."""
        stopped = self.shutdown_workers()
        if event is not None:
            if stopped:
                event.accept()
            else:
                # Keep the widgets and their owning Python references alive.
                # A later close attempt can finish after the cooperative
                # cancellation reaches the worker.
                event.ignore()


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
