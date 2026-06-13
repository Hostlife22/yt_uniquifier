"""v0.7.0 R7 — WCAG-AA contrast regression for theme tokens.

E4 (v0.7.0 R1) moved every status-badge / KPI-pill hex into
``yt_uniquifier.gui.theme.{DARK_TOKENS,LIGHT_TOKENS}`` so theme switches
no longer leak hardcoded colours.  This test guards the *other* half
of E4: every foreground / background **pair** the GUI actually paints
must clear WCAG 2.1 AA contrast (≥ 4.5:1 for normal text).

If a future redesign nudges a fill towards its fg, this test fires
*before* the change ships — much better than a screen-reader user
discovering a 2.7:1 yellow-on-yellow badge in production.

Pure-Python: no Qt instantiation needed because we read the tokens
straight from ``theme`` and reproduce the WCAG luminance formula by
hand.  Runs in <50 ms.
"""

from __future__ import annotations

import pytest

from yt_uniquifier.gui.theme import DARK_TOKENS, LIGHT_TOKENS

# WCAG 2.1 SC 1.4.3 Level AA for normal-size text.  Large text (≥18pt
# bold or ≥24pt regular) only needs 3.0:1, but we don't render anything
# in that band — applying the stricter bar everywhere costs nothing.
_AA_MIN_RATIO = 4.5


def _hex_to_rgb(token: str) -> tuple[float, float, float]:
    """Parse ``#rrggbb`` → ``(r, g, b)`` in [0, 1]. Strict — three-char
    shorthands and rgba() are rejected so a typo can't masquerade as
    a darker / lighter shade and silently pass contrast.
    """
    if not token.startswith("#") or len(token) != 7:
        raise ValueError(f"expected 7-char #rrggbb token, got {token!r}")
    r = int(token[1:3], 16) / 255.0
    g = int(token[3:5], 16) / 255.0
    b = int(token[5:7], 16) / 255.0
    return r, g, b


def _channel_lum(c: float) -> float:
    """sRGB → relative luminance for one channel (WCAG SC 1.4.3 formula)."""
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(token: str) -> float:
    """Relative luminance of a colour token ∈ [0, 1]."""
    r, g, b = _hex_to_rgb(token)
    return (
        0.2126 * _channel_lum(r)
        + 0.7152 * _channel_lum(g)
        + 0.0722 * _channel_lum(b)
    )


def contrast_ratio(fg: str, bg: str) -> float:
    """Symmetric contrast ratio between two colour tokens (1.0 – 21.0)."""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# Every (fg, bg) pair the QSS template + widget painters actually
# render.  Keep this list as the contract: if a new widget introduces
# a new pair the developer adds it here (one line) and the test
# becomes the regression guard.
#
# `label` is what fails surface — "kpi_yellow / kpi_fg" is far
# easier to chase down than "(d1a93b, ffffff)".
_PAIRS: list[tuple[str, str, str]] = [
    # body text
    ("fg",            "bg",            "primary body text"),
    ("fg",            "bg_alt",        "body text on raised surface"),
    ("fg",            "bg_deep",       "body text on log-console / sidebar"),
    ("fg_dim",        "bg",            "secondary text (status, path label)"),
    ("fg_dim",        "bg_deep",       "sidebar item idle state"),
    # status badges (E4 — preflight_panel + kpi_pills)
    ("badge_fail_fg", "badge_fail_bg", "preflight FAIL badge"),
    ("badge_warn_fg", "badge_warn_bg", "preflight WARN badge"),
    ("badge_ok_fg",   "badge_ok_bg",   "preflight OK badge"),
    # KPI pills (kpi_pills.py — per-band fg since R7 / WCAG-AA fix).
    ("kpi_red_fg",     "kpi_red",     "KPI red band"),
    ("kpi_yellow_fg",  "kpi_yellow",  "KPI yellow band"),
    ("kpi_green_fg",   "kpi_green",   "KPI green band"),
    ("kpi_neutral_fg", "kpi_neutral", "KPI neutral band"),
]


@pytest.mark.parametrize("theme,tokens", [
    ("dark",  DARK_TOKENS),
    ("light", LIGHT_TOKENS),
])
@pytest.mark.parametrize("fg_key,bg_key,label", _PAIRS)
def test_token_pair_meets_wcag_aa(
    theme: str,
    tokens: dict[str, str],
    fg_key: str,
    bg_key: str,
    label: str,
) -> None:
    fg = tokens[fg_key]
    bg = tokens[bg_key]
    ratio = contrast_ratio(fg, bg)
    assert ratio >= _AA_MIN_RATIO, (
        f"{theme} theme: {label} fails WCAG-AA "
        f"({fg_key}={fg} on {bg_key}={bg} → {ratio:.2f}:1, need ≥{_AA_MIN_RATIO}:1). "
        "Pick a darker fill or a lighter fg in theme.py."
    )


# Sanity checks on the formula itself — protect against a future refactor
# that quietly inverts the ratio direction (a 1:21 ratio is unrenderable
# in 8-bit sRGB; if we accidentally returned reciprocals it would still
# *look* high enough on dark themes and only break on the next palette
# swap).

def test_relative_luminance_bounds() -> None:
    assert relative_luminance("#000000") == 0.0
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    # Pure grey midpoint is well-defined.
    mid = relative_luminance("#808080")
    assert 0.2 < mid < 0.3


def test_contrast_ratio_is_symmetric() -> None:
    """contrast_ratio(a, b) == contrast_ratio(b, a)."""
    a, b = "#000000", "#ffffff"
    assert contrast_ratio(a, b) == pytest.approx(contrast_ratio(b, a))
    # White on black is the maximum: 21.0:1 by definition.
    assert contrast_ratio(a, b) == pytest.approx(21.0, rel=1e-3)


def test_hex_parser_rejects_short_form() -> None:
    """`#fff` is a real CSS shorthand but every yt-uniquifier token is
    long form — a stray `#abc` could quietly resolve to a different
    colour than the author intended, so the parser is strict."""
    with pytest.raises(ValueError, match="expected 7-char"):
        _hex_to_rgb("#fff")
    with pytest.raises(ValueError, match="expected 7-char"):
        _hex_to_rgb("e2e2e8")  # missing #
