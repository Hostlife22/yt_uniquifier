"""SegmentTimeline — horizontal bar of coloured cells per segment."""

from __future__ import annotations

from typing import Literal

from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

SegmentStatus = Literal["pending", "in_progress", "done", "failed"]

_COLORS = {
    "pending":     QColor("#444"),
    "in_progress": QColor("#3b6ea8"),
    "done":        QColor("#3ba85c"),
    "failed":      QColor("#a83b3b"),
}


class SegmentTimeline(QWidget):
    """N coloured cells representing per-segment status.

    Public API:
      init(n_segments)
      update_segment(idx, status)
      reset()
    """

    def __init__(self) -> None:
        super().__init__()
        self._statuses: list[SegmentStatus] = []
        self.setFixedHeight(24)

    def init(self, n_segments: int) -> None:
        self._statuses = ["pending"] * n_segments
        self.update()

    def update_segment(self, idx: int, status: SegmentStatus) -> None:
        if 0 <= idx < len(self._statuses):
            self._statuses[idx] = status
            self.update()

    def reset(self) -> None:
        self._statuses = []
        self.update()

    def statuses(self) -> list[SegmentStatus]:
        """Read-only snapshot (test introspection)."""
        return list(self._statuses)

    def paintEvent(self, _event: object) -> None:
        if not self._statuses:
            return
        painter = QPainter(self)
        try:
            w = self.width()
            h = self.height()
            n = len(self._statuses)
            cell_w = w / n
            gap = 1
            for i, status in enumerate(self._statuses):
                x = int(i * cell_w) + gap
                cw = max(2, int(cell_w) - 2 * gap)
                painter.fillRect(
                    x, gap, cw, h - 2 * gap,
                    _COLORS.get(status, _COLORS["pending"]),
                )
        finally:
            painter.end()
