"""KpiPills — horizontal chips showing KPI values from a qa.json."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from yt_uniquifier.gui.theme import tokens_for

# Threshold bands per KPI: (green_max, yellow_max). Beyond → red.
# Values use the "lower is better" convention where applicable
# (pHash, cid_predict). For "higher is better" KPIs (VMAF, Hamming)
# we flip the comparison in _pill_color_key.
_KPI_BANDS = {
    "phash_worst":   ("lower", 0.75, 0.85),
    "vmaf_mean":     ("higher", 85.0, 75.0),
    "audio_hamming": ("higher", 18.0, 10.0),
    "cid_predict":   ("lower", 0.2, 0.4),
}


def _pill_color_key(name: str, value: float | None) -> str:
    """Return the token key (`kpi_red`/`kpi_yellow`/`kpi_green`/`kpi_neutral`).

    Decoupled from the actual color string so the same logic resolves
    differently per theme (R1/E4 — was returning hex codes directly,
    which leaked dark-theme palette into light-theme renders).
    """
    if value is None:
        return "kpi_neutral"
    direction, green_thr, yellow_thr = _KPI_BANDS.get(name, ("lower", 0.5, 0.8))
    if direction == "lower":
        if value < green_thr:
            return "kpi_green"
        if value < yellow_thr:
            return "kpi_yellow"
        return "kpi_red"
    # higher
    if value >= green_thr:
        return "kpi_green"
    if value >= yellow_thr:
        return "kpi_yellow"
    return "kpi_red"


class KpiPills(QWidget):
    """Horizontal row of colored chips for QA KPIs.

    Empty when no qa.json is set. Renders 4 pills:
    pHash worst chunk, VMAF, Audio FP Hamming, CID predicted.

    Pass `state` to subscribe to `theme_changed` so the pills repaint
    on theme switch. Construction without `state` keeps the widget
    usable in tests; pills will use the dark theme until
    `set_theme()` is called explicitly.
    """

    def __init__(self, state: object | None = None) -> None:
        super().__init__()
        self._theme: str = "dark"
        self._last_qa: dict[str, object] | None = None
        self._build_ui()
        if state is not None:
            theme = getattr(state, "theme", None)
            if isinstance(theme, str):
                self._theme = theme
            sig = getattr(state, "theme_changed", None)
            if sig is not None:
                sig.connect(self.set_theme)

    def _build_ui(self) -> None:
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)

    def set_theme(self, theme: str) -> None:
        """Repaint pills with the new theme's tokens. Idempotent."""
        if theme == self._theme:
            return
        self._theme = theme
        if self._last_qa is not None:
            self.set_qa(self._last_qa)

    def clear(self) -> None:
        while self._layout.count() > 1:  # keep stretch at the tail
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_qa(self, qa: dict[str, object]) -> None:
        """Populate from a QA dict (decoded qa.json)."""
        self._last_qa = qa
        self.clear()

        # Compute worst chunk pHash.
        chunks_raw = qa.get("chunk_similarities") or []
        chunks: list[dict[str, float]] = chunks_raw if isinstance(chunks_raw, list) else []
        phash_fallback = qa.get("phash_similarity")
        phash_default = phash_fallback if isinstance(phash_fallback, (int, float)) else None
        worst_phash: float | None = max(
            (
                float(c.get("combined", c.get("visual", 0.0)))
                for c in chunks if isinstance(c, dict)
            ),
            default=phash_default,
        )

        def _opt_float(key: str) -> float | None:
            v = qa.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        pills = [
            ("pHash worst", worst_phash, "phash_worst", "{:.3f}"),
            ("VMAF", _opt_float("vmaf_mean"), "vmaf_mean", "{:.1f}"),
            (
                "Audio Hamming",
                _opt_float("audio_fp_hamming_per_frame"),
                "audio_hamming",
                "{:.1f}b",
            ),
            ("CID predicted", _opt_float("cid_predict_self"), "cid_predict", "{:.2f}"),
        ]
        for label, value, band_key, fmt in pills:
            self._layout.insertWidget(self._layout.count() - 1,
                                       self._pill(label, value, band_key, fmt))

    def _pill(
        self, label: str, value: float | None, band_key: str, fmt: str,
    ) -> QLabel:
        tokens = tokens_for(self._theme)
        color = tokens[_pill_color_key(band_key, value)]
        fg = tokens["kpi_fg"]
        text = fmt.format(value) if value is not None else "n/a"
        pill = QLabel(f"<b>{label}</b>  {text}")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            f"background: {color}; color: {fg}; "
            f"padding: 6px 12px; border-radius: 12px; font-weight: 600;"
        )
        return pill
