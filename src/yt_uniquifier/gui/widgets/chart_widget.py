"""Minimal multi-series line chart with QPainter fallback when QtCharts is absent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtCharts import (
        QChart,
        QChartView,
        QLineSeries,
    )
    HAS_QTCHARTS = True
except ImportError:
    HAS_QTCHARTS = False


@dataclass
class Series:
    name: str
    color: str                                    # CSS hex e.g. "#3b6ea8"
    points: list[tuple[float, float]] = field(default_factory=list)


class ChartWidget(QWidget):
    """Render N line series.

    Backend depends on QtCharts availability. Public API is identical
    either way:

      set_series(list[Series])  — replace all series, re-render.
      add_point(name, x, y)     — append a point to named series.
      clear()                    — drop all series.
    """

    def __init__(self) -> None:
        super().__init__()
        self._series: list[Series] = []
        self._lines: dict[str, object] = {}  # name → QLineSeries (qtcharts only)
        self.setMinimumHeight(200)
        self._chart: object | None = None
        if HAS_QTCHARTS:
            self._build_qtcharts()

    def set_series(self, series: list[Series]) -> None:
        self._series = list(series)
        self._lines.clear()
        self._refresh()

    def add_point(self, name: str, x: float, y: float) -> None:
        """Append a single point.

        Calibration emits up to 3 add_point calls per step. The earlier
        implementation called _refresh on every call which did
        chart.removeAllSeries() + a full rebuild — O(N²) total work
        across an iteration. Append to the retained QLineSeries
        directly so each add_point is O(1).
        """
        existing = next((s for s in self._series if s.name == name), None)
        if existing is None:
            existing = Series(name=name, color="#3b6ea8", points=[])
            self._series.append(existing)
        existing.points.append((x, y))

        if HAS_QTCHARTS and self._chart is not None:
            line = self._lines.get(name)
            if line is None:
                line = QLineSeries()
                line.setName(name)
                pen = QPen(QColor(existing.color))
                pen.setWidth(2)
                line.setPen(pen)
                chart = cast(QChart, self._chart)
                chart.addSeries(line)
                self._lines[name] = line
                # Re-create default axes on first point of a new series
                # so the line is rendered with proper scales.
                chart.createDefaultAxes()
            cast(QLineSeries, line).append(QPointF(x, y))
        else:
            self.update()  # paintEvent fallback

    def clear(self) -> None:
        self._series = []
        self._lines.clear()
        self._refresh()

    # ---- backends ----
    def _build_qtcharts(self) -> None:
        chart = QChart()
        chart.legend().setVisible(True)
        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        self._chart = chart
        self._view = view

    def _refresh(self) -> None:
        if HAS_QTCHARTS and self._chart is not None:
            chart: QChart = self._chart
            chart.removeAllSeries()
            # Repopulate `_lines` from the freshly-added QLineSeries so
            # later `add_point` calls find the live series instead of
            # creating a duplicate. Without this the second iteration of
            # a calibration drew every series twice (once from
            # set_series, once from add_point).
            self._lines.clear()
            for s in self._series:
                line = QLineSeries()
                line.setName(s.name)
                pen = QPen(QColor(s.color))
                pen.setWidth(2)
                line.setPen(pen)
                for x, y in s.points:
                    line.append(QPointF(x, y))
                chart.addSeries(line)
                self._lines[s.name] = line
            chart.createDefaultAxes()
        else:
            self.update()  # triggers paintEvent (fallback)

    def paintEvent(self, _event: object) -> None:
        if HAS_QTCHARTS:
            return
        if not self._series or not any(s.points for s in self._series):
            return
        painter = QPainter(self)
        try:
            self._paint_fallback(painter)
        finally:
            painter.end()

    def _paint_fallback(self, painter: QPainter) -> None:
        w = self.width()
        h = self.height()
        margin = 30
        chart_w = max(10, w - 2 * margin)
        chart_h = max(10, h - 2 * margin)

        # Bounds across all series.
        all_x = [p[0] for s in self._series for p in s.points]
        all_y = [p[1] for s in self._series for p in s.points]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        span_x = max_x - min_x or 1.0
        span_y = max_y - min_y or 1.0

        # Axes.
        axis_pen = QPen(QColor("#555"))
        painter.setPen(axis_pen)
        painter.drawLine(margin, h - margin, w - margin, h - margin)
        painter.drawLine(margin, margin, margin, h - margin)

        # Series.
        for s in self._series:
            if not s.points:
                continue
            pen = QPen(QColor(s.color))
            pen.setWidth(2)
            painter.setPen(pen)
            prev_pt: tuple[float, float] | None = None
            for x, y in s.points:
                px = margin + int((x - min_x) / span_x * chart_w)
                py = h - margin - int((y - min_y) / span_y * chart_h)
                if prev_pt is not None:
                    ppx, ppy = prev_pt
                    painter.drawLine(int(ppx), int(ppy), px, py)
                prev_pt = (px, py)

        # Mini legend top-right.
        painter.setPen(QPen(QColor("#9b9ba8")))
        for i, s in enumerate(self._series):
            painter.fillRect(
                w - margin - 80, margin + i * 14, 10, 10, QColor(s.color),
            )
            painter.drawText(w - margin - 65, margin + i * 14 + 10, s.name)

        # Skip Qt.AlignmentFlag — drawText takes int coords directly.
        _ = Qt.AlignmentFlag.AlignLeft  # no-op; kept to silence "imported"
