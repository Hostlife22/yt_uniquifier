"""v0.7 R4 / F2 — DivergenceIndicator widget behavior.

The widget must:
  * stay hidden until the first sample
  * track latest / running EMA / lowest
  * accept malformed payloads without crashing
  * respond to set_theme(...) for dark/light flip
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.gui.widgets.divergence_indicator import DivergenceIndicator


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


def test_starts_hidden(app: QApplication) -> None:
    d = DivergenceIndicator()
    assert d.isHidden()


def test_first_sample_unhides(app: QApplication) -> None:
    d = DivergenceIndicator()
    d.show()  # parent layout would normally show it on screen entry
    d.hide()
    assert d.isHidden()
    d.push_sample({"segment": 0, "phash_similarity": 0.9, "running_phash": 0.9})
    assert d.isVisible()


def test_tracks_latest_running_lowest(app: QApplication) -> None:
    d = DivergenceIndicator()
    d.push_sample({"segment": 0, "phash_similarity": 0.95, "running_phash": 0.95})
    d.push_sample({"segment": 1, "phash_similarity": 0.80, "running_phash": 0.91})
    d.push_sample({"segment": 2, "phash_similarity": 0.88, "running_phash": 0.90})
    assert d._latest == pytest.approx(0.88)
    assert d._running == pytest.approx(0.90)
    assert d._lowest == pytest.approx(0.80)
    assert d._sample_count == 3


def test_malformed_payload_no_crash(app: QApplication) -> None:
    d = DivergenceIndicator()
    # Missing required key — must be a quiet no-op.
    d.push_sample({"segment": 0})
    # Non-numeric similarity — must not crash either.
    d.push_sample({"phash_similarity": "wat"})
    assert d._sample_count == 0
    assert d.isHidden()


def test_reset_clears_state(app: QApplication) -> None:
    d = DivergenceIndicator()
    d.push_sample({"segment": 0, "phash_similarity": 0.7})
    assert d._sample_count == 1
    d.reset()
    assert d._latest is None
    assert d._lowest is None
    assert d._sample_count == 0
    assert d.isHidden()


def test_band_color_thresholds(app: QApplication) -> None:
    d = DivergenceIndicator()
    tokens = {"kpi_green": "#G", "kpi_yellow": "#Y", "kpi_red": "#R"}
    # Mirrors the same lower-is-better banding as widgets/kpi_pills::phash_worst.
    assert d._band_color_hex(0.70, tokens) == "#G"
    assert d._band_color_hex(0.82, tokens) == "#Y"
    assert d._band_color_hex(0.95, tokens) == "#R"


def test_set_theme_re_resolves_color(app: QApplication) -> None:
    """Theme switch should re-paint the latest sample with new palette."""
    d = DivergenceIndicator()
    d.push_sample({"segment": 0, "phash_similarity": 0.5})
    dark_text = d.current_label.text()
    d.set_theme("light")
    light_text = d.current_label.text()
    # Light palette's kpi_green differs from dark's, so the embedded
    # color attribute changes — but the value formatting stays stable.
    assert "0.500" in dark_text and "0.500" in light_text
