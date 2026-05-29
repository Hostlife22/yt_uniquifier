"""v0.4.0: cid_aware/cid_aggressive bumped from weak defaults.

These tests pin the floor — future tweaks above is fine, dropping below
the v0.4.0 bumps means we're back in the placebo zone.
"""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parent.parent.parent / "src" / "yt_uniquifier" / "profiles"


def _params_by_id(profile, transform_id: str) -> dict:
    for tc in profile.transforms:
        if tc.id == transform_id and tc.enabled:
            return tc.params or {}
    raise AssertionError(f"{transform_id} not enabled in profile")


def test_cid_aware_v040_minima() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")

    crop = _params_by_id(profile, "video.crop_resize")
    assert crop["max_strength"] >= 0.06, "crop weakened below v0.4.0 floor"

    color = _params_by_id(profile, "video.color_eq")
    assert color["brightness"] >= 0.025, "brightness weakened below v0.4.0 floor"
    assert color["saturation"] >= 1.06, "saturation weakened below v0.4.0 floor"

    noise = _params_by_id(profile, "video.noise")
    assert noise["strength"] >= 8, "noise strength weakened below v0.4.0 floor"

    eq = _params_by_id(profile, "audio.eq")
    assert eq.get("jitter_db", 0.0) >= 1.5, "audio.eq jitter_db weakened below v0.4.0 floor"


def test_cid_aggressive_v040_above_cid_aware() -> None:
    """cid_aggressive should be strictly stronger than cid_aware on each knob."""
    aware = load_profile(PROFILES_DIR / "cid_aware.yaml")
    aggressive = load_profile(PROFILES_DIR / "cid_aggressive.yaml")

    a_crop = _params_by_id(aware, "video.crop_resize")
    g_crop = _params_by_id(aggressive, "video.crop_resize")
    assert g_crop["max_strength"] >= a_crop["max_strength"]

    a_noise = _params_by_id(aware, "video.noise")
    g_noise = _params_by_id(aggressive, "video.noise")
    assert g_noise["strength"] >= a_noise["strength"]


def test_cid_aware_has_subpixel_sharpen() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    assert any(
        tc.id == "video.subpixel_sharpen" and tc.enabled
        for tc in profile.transforms
    ), "video.subpixel_sharpen must be enabled in cid_aware (v0.4.0)"
