"""Background probe for the Settings → Notifications → Test button.

Calls ``core.notifications.dispatch`` with a synthetic
``NotificationContext`` so the user can verify their webhook / SMTP
config without running a full encode.  Routes the dispatch log lines
back through Qt signals so the GUI shows whatever happened (success
status, transport error, non-2xx response, SMTP auth fail) inline
instead of via a modal dialog the user has to dismiss.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.notifications import (
    NotificationConfig,
    NotificationContext,
    dispatch,
)
from yt_uniquifier.gui.workers.base import WorkerBase


class NotificationsTestWorker(WorkerBase):
    """One-shot worker that calls dispatch() on a NotificationConfig."""

    # `dispatch` reports each channel's outcome through the logger;
    # forward those verbatim to the screen.
    line = pyqtSignal(str, str)        # (message, level: "info"|"warn"|"error")

    def __init__(self, config: NotificationConfig) -> None:
        super().__init__()
        self.config = config

    def run(self) -> None:
        ctx = NotificationContext(
            event_kind="completed",
            title="yt-uniquifier — test notification",
            body=(
                "This is a test notification from the Settings screen.\n"
                "If you can read this in Discord / Slack / Telegram / email, "
                "your webhook + SMTP setup is wired up correctly."
            ),
            fields={"source": "Settings → Test button", "scope": "smoke"},
        )

        def _emit(msg: str, level: str) -> None:
            self.line.emit(msg, level)

        try:
            dispatch(self.config, ctx, logger=_emit)
        except Exception as exc:  # noqa: BLE001 — never propagate
            # dispatch() is itself never-raise by contract, but a
            # malformed config could throw during context build; keep
            # the worker robust.
            self.line.emit(f"test dispatch error: {exc}", "error")
        # Emit `finished_ok` so the screen can re-enable the button.
        self.finished_ok.emit({"ok": True})
