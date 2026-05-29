"""KpiPills — horizontal chips showing KPI values from a qa.json."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

# Threshold bands per KPI: (green_max, yellow_max). Beyond → red.
# Values use the "lower is better" convention where applicable
# (pHash, cid_predict). For "higher is better" KPIs (VMAF, Hamming)
# we flip the comparison in _pill_color.
_KPI_BANDS = {
    "phash_worst":   ("lower", 0.75, 0.85),
    "vmaf_mean":     ("higher", 85.0, 75.0),
    "audio_hamming": ("higher", 18.0, 10.0),
    "cid_predict":   ("lower", 0.2, 0.4),
}


def _pill_color(name: str, value: float | None) -> str:
    if value is None:
        return "#9b9ba8"
    direction, green_thr, yellow_thr = _KPI_BANDS.get(name, ("lower", 0.5, 0.8))
    if direction == "lower":
        if value < green_thr:
            return "#3ba85c"   # green
        if value < yellow_thr:
            return "#d1a93b"   # yellow
        return "#a83b3b"       # red
    # higher
    if value >= green_thr:
        return "#3ba85c"
    if value >= yellow_thr:
        return "#d1a93b"
    return "#a83b3b"


class KpiPills(QWidget):
    """Horizontal row of colored chips for QA KPIs.

    Empty when no qa.json is set. Renders 4 pills:
    pHash worst chunk, VMAF, Audio FP Hamming, CID predicted.
    """

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)

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
        color = _pill_color(band_key, value)
        text = fmt.format(value) if value is not None else "n/a"
        pill = QLabel(f"<b>{label}</b>  {text}")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            f"background: {color}; color: white; "
            f"padding: 6px 12px; border-radius: 12px; font-weight: 600;"
        )
        return pill
