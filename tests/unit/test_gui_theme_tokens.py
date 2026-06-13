"""v0.7.0 R1 / E4 — theme-token regression for badges + KPI pills.

Pre-R1, badges in `widgets/preflight_panel.py` and pills in
`widgets/kpi_pills.py` hard-coded their hex colors. A switch from
dark → light theme (or vice versa) did not repaint them, leaving
dark-theme reds on a near-white background (low-contrast leak).

These tests verify that:
  1. `tokens_for()` returns full badge_* + kpi_* coverage in both themes.
  2. Constructing the widgets without a state still works (test usability).
  3. `set_theme()` flips the rendered stylesheet between themes.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.core.preflight import PreflightFinding
from yt_uniquifier.gui.theme import tokens_for
from yt_uniquifier.gui.widgets.kpi_pills import KpiPills
from yt_uniquifier.gui.widgets.preflight_panel import PreflightPanel


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


_REQUIRED_BADGE_KEYS = {
    "badge_fail_bg", "badge_fail_fg",
    "badge_warn_bg", "badge_warn_fg",
    "badge_ok_bg",   "badge_ok_fg",
}
_REQUIRED_KPI_KEYS = {
    "kpi_red", "kpi_yellow", "kpi_green", "kpi_neutral", "kpi_fg",
}


def test_tokens_cover_badges_and_kpis_for_both_themes() -> None:
    for theme in ("dark", "light"):
        tokens = tokens_for(theme)  # type: ignore[arg-type]
        missing = (_REQUIRED_BADGE_KEYS | _REQUIRED_KPI_KEYS) - set(tokens.keys())
        assert not missing, f"{theme} missing tokens: {missing}"
        # Every required token must be a non-empty hex string.
        for k in _REQUIRED_BADGE_KEYS | _REQUIRED_KPI_KEYS:
            v = tokens[k]
            assert v.startswith("#") and len(v) in (4, 7, 9), f"{theme}.{k}={v!r}"


def test_preflight_panel_badge_repaints_on_theme_change(app: QApplication) -> None:
    """Theme switch must invalidate the rendered badge style.

    Uses a `warn` finding because `badge_warn_bg` differs between
    dark (`#d1a93b`) and light (`#b8902f`) palettes — gives a
    visible diff that proves the QSS was re-applied. (`fail` bg is
    identical across themes by design, so it can't catch this leak.)
    """
    panel = PreflightPanel()
    panel.set_findings([
        PreflightFinding(code="t.x", severity="warn", message="m", suggestion=None),
    ])
    badge = _first_badge(panel)
    dark_style = badge.styleSheet()
    panel.set_theme("light")
    badge2 = _first_badge(panel)
    light_style = badge2.styleSheet()
    assert dark_style != light_style, (
        "badge stylesheet didn't change after theme switch "
        f"(theme leak): {dark_style!r}"
    )


def test_kpi_pills_repaint_on_theme_change(app: QApplication) -> None:
    pills = KpiPills()
    pills.set_qa({
        "phash_similarity": 0.5,
        "vmaf_mean": 90.0,
        "audio_fp_hamming_per_frame": 19.0,
        "cid_predict_self": 0.1,
        "chunk_similarities": [],
    })
    first = _first_pill_label(pills)
    dark = first.styleSheet()
    pills.set_theme("light")
    first2 = _first_pill_label(pills)
    light = first2.styleSheet()
    # In our palette, several kpi_* tokens differ between themes
    # (kpi_yellow + kpi_green), so at least one pill must repaint.
    assert dark != light, f"pills did not repaint on theme switch: {dark!r}"


def test_widgets_constructable_without_state(app: QApplication) -> None:
    """state=None must remain a valid construction mode for tests."""
    panel = PreflightPanel(state=None)
    assert panel is not None
    pills = KpiPills(state=None)
    assert pills is not None


# ---- helpers ----
def _first_badge(panel: PreflightPanel):
    """First QLabel inside the first finding row."""
    from PyQt6.QtWidgets import QFrame, QLabel
    for i in range(panel._layout.count()):
        item = panel._layout.itemAt(i)
        if item is None:
            continue
        w = item.widget()
        if isinstance(w, QFrame):
            for child in w.findChildren(QLabel):
                if child.text() in ("FAIL", "WARN", "OK"):
                    return child
    raise AssertionError("no badge label found")


def _first_pill_label(pills: KpiPills):
    from PyQt6.QtWidgets import QLabel
    for i in range(pills._layout.count()):
        item = pills._layout.itemAt(i)
        if item is None:
            continue
        w = item.widget()
        if isinstance(w, QLabel):
            return w
    raise AssertionError("no pill label found")
