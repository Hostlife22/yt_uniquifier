"""Smoke tests for the heatmap_color jinja filter."""

from __future__ import annotations

from yt_uniquifier.core.qa.report import heatmap_color


def test_low_is_green() -> None:
    s = heatmap_color(0.0)
    assert s.startswith("hsl(120,")  # hue 120 = green


def test_mid_is_yellow() -> None:
    s = heatmap_color(0.5)
    # 60° hue = yellow
    assert s.startswith("hsl(60,")


def test_high_is_red() -> None:
    s = heatmap_color(1.0)
    assert s.startswith("hsl(0,")


def test_clamped_below_zero() -> None:
    assert heatmap_color(-0.5).startswith("hsl(120,")


def test_clamped_above_one() -> None:
    assert heatmap_color(1.5).startswith("hsl(0,")
