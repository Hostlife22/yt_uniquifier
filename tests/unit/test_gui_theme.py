"""theme.py — QSS token interpolation."""

from __future__ import annotations

from yt_uniquifier.gui.theme import DARK_TOKENS, LIGHT_TOKENS, qss_for


def test_qss_dark_contains_dark_bg() -> None:
    qss = qss_for("dark")
    assert DARK_TOKENS["bg"] in qss
    assert DARK_TOKENS["accent"] in qss


def test_qss_light_contains_light_bg() -> None:
    qss = qss_for("light")
    assert LIGHT_TOKENS["bg"] in qss
    assert LIGHT_TOKENS["accent"] in qss


def test_qss_system_falls_back_to_dark() -> None:
    qss = qss_for("system")
    assert DARK_TOKENS["bg"] in qss


def test_qss_includes_sidebar_styling() -> None:
    """Sidebar styling is critical for navigation visual feedback."""
    qss = qss_for("dark")
    assert "QListWidget#sidebar" in qss
