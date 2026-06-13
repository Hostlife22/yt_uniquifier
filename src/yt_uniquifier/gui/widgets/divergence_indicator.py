"""Live divergence indicator (v0.7 R4 / F2).

Displays the per-segment pHash similarity stream emitted by
``orchestrator._maybe_emit_divergence``.  Three numbers + a tiny
inline sparkline of the most recent samples.  Theme-aware via
``gui.theme.tokens_for`` so it repaints cleanly on dark↔light flip.

Lower similarity → more divergent encoded output → more unique vs
source.  Anchors for the user's eye:

  * similarity ≈ 1.00 — bit-perfect (no real transform happened)
  * similarity ≈ 0.95 — typical for "soft" profiles
  * similarity ≈ 0.85 — "medium" / "cid_aware"
  * similarity ≲ 0.80 — "aggressive" / pad_blur / mirror

The widget is silent (``hide()``) until the first sample arrives,
so off-mode runs don't show a stale zero.
"""

from __future__ import annotations

from collections import deque

from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from yt_uniquifier.gui.theme import tokens_for


class _Sparkline(QWidget):
    """Compact horizontal line of the last N pHash samples.

    Custom-painted with QPainter instead of a full chart so the
    widget stays cheap to update at the per-segment cadence even on
    long runs (1000+ segments) and so it has no QtCharts dependency
    (the project's `chart_widget.py` is fine for the Calibrate
    screen but bigger than this row deserves).
    """

    def __init__(self, capacity: int = 30, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._capacity = capacity
        self._samples: deque[float] = deque(maxlen=capacity)
        self._theme: str = "dark"
        self.setMinimumWidth(120)
        self.setMinimumHeight(28)

    def push(self, value: float) -> None:
        self._samples.append(value)
        self.update()

    def clear(self) -> None:
        self._samples.clear()
        self.update()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        if not self._samples:
            return
        tokens = tokens_for(self._theme)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            pad_x, pad_y = 4, 4
            usable_w = max(w - 2 * pad_x, 1)
            usable_h = max(h - 2 * pad_y, 1)
            # All similarity values live in [0..1]; we scale that into
            # the usable strip so the spread of changes is visible
            # rather than collapsed against the top edge.
            lo = max(min(self._samples) - 0.02, 0.0)
            hi = min(max(self._samples) + 0.02, 1.0)
            rng = max(hi - lo, 1e-6)
            n = len(self._samples)
            step = usable_w / max(n - 1, 1)
            pen = QPen(QColor(tokens.get("accent", "#3b6ea8")))
            pen.setWidth(2)
            painter.setPen(pen)
            prev_x: float = float(pad_x)
            prev_y: float = pad_y + usable_h * (1 - (self._samples[0] - lo) / rng)
            for i in range(1, n):
                x = pad_x + step * i
                y = pad_y + usable_h * (1 - (self._samples[i] - lo) / rng)
                painter.drawLine(int(prev_x), int(prev_y), int(x), int(y))
                prev_x, prev_y = x, y
        finally:
            painter.end()


class DivergenceIndicator(QWidget):
    """Row of {current, EMA, lowest} numbers + sparkline.

    Consumes the ``divergence_sample`` payload routed from
    ``RunWorker``: ``{phash_similarity, running_phash, segment}``.
    Hidden until the first sample arrives so off-mode runs don't
    show a stale row.  ``set_theme`` flips colors on theme change.
    """

    def __init__(self, state: object | None = None) -> None:
        super().__init__()
        self._theme: str = "dark"
        self._latest: float | None = None
        self._running: float | None = None
        self._lowest: float | None = None
        self._sample_count: int = 0
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        self.title_label = QLabel("<b>Divergence</b>")
        self.title_label.setAccessibleName("Live divergence indicator")
        self.title_label.setAccessibleDescription(
            "Per-segment perceptual hash similarity between source and encoded output. "
            "Lower numbers mean more visual divergence (more unique).",
        )
        layout.addWidget(self.title_label)

        self.current_label = QLabel("now: —")
        self.current_label.setToolTip("Most recent segment's source↔encoded similarity")
        layout.addWidget(self.current_label)

        self.running_label = QLabel("avg: —")
        self.running_label.setToolTip("Exponential moving average across segments")
        layout.addWidget(self.running_label)

        self.lowest_label = QLabel("min: —")
        self.lowest_label.setToolTip("Lowest single-segment similarity seen so far")
        layout.addWidget(self.lowest_label)

        self.sparkline = _Sparkline(capacity=30)
        layout.addWidget(self.sparkline, stretch=1)

        self.count_label = QLabel("samples: 0")
        self.count_label.setObjectName("status")
        layout.addWidget(self.count_label)

    def set_theme(self, theme: str) -> None:
        if theme == self._theme:
            return
        self._theme = theme
        self.sparkline.set_theme(theme)
        # Push the latest value back through the formatter so its color
        # band re-resolves against the new palette.
        if self._latest is not None:
            self._apply_text()

    # ---- public API ----
    def reset(self) -> None:
        self._latest = self._running = self._lowest = None
        self._sample_count = 0
        self.sparkline.clear()
        self.hide()

    def push_sample(self, payload: dict[str, object]) -> None:
        """Consume one `divergence_sample` RunEvent payload.

        Tolerant of missing / non-numeric keys — divergence is an
        opt-in observability feature and a malformed payload should
        never blow up the screen.
        """
        sim = payload.get("phash_similarity")
        running = payload.get("running_phash")
        if not isinstance(sim, (int, float)):
            return
        self._latest = float(sim)
        if isinstance(running, (int, float)):
            self._running = float(running)
        if self._lowest is None or self._latest < self._lowest:
            self._lowest = self._latest
        self._sample_count += 1
        self.sparkline.push(self._latest)
        self._apply_text()
        if self.isHidden():
            self.show()

    # ---- internals ----
    def _apply_text(self) -> None:
        if self._latest is None:
            return
        tokens = tokens_for(self._theme)
        color = self._band_color_hex(self._latest, tokens)
        self.current_label.setText(
            f'<span style="color:{color}">now: <b>{self._latest:.3f}</b></span>',
        )
        if self._running is not None:
            self.running_label.setText(f"avg: <b>{self._running:.3f}</b>")
        if self._lowest is not None:
            min_color = self._band_color_hex(self._lowest, tokens)
            self.lowest_label.setText(
                f'<span style="color:{min_color}">min: '
                f"<b>{self._lowest:.3f}</b></span>",
            )
        self.count_label.setText(f"samples: {self._sample_count}")

    @staticmethod
    def _band_color_hex(value: float, tokens: dict[str, str]) -> str:
        """Map similarity into the project's KPI color tokens.

        Reuses the same three-band convention `KpiPills` applies to
        the pHash KPI (see `widgets/kpi_pills.py::_KPI_BANDS`):
        lower = better. Anchored to those thresholds so theme palette
        tweaks repaint here for free.
        """
        if value < 0.75:
            return tokens.get("kpi_green") or "#3ba85c"
        if value < 0.85:
            return tokens.get("kpi_yellow") or "#d1a93b"
        return tokens.get("kpi_red") or "#a83b3b"
