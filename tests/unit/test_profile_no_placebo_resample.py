"""v0.4.0: audio.resample 48000↔47999 is below chromaprint quantization.

Regression guard: keep it disabled in CID-aware profiles. If a future
release wants to use it, pick an intermediate_sr that's actually audible
(e.g. 47000 or 49000 — but those overlap with audio.pitch_tempo).
"""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parent.parent.parent / "src" / "yt_uniquifier" / "profiles"


def _enabled_state(profile, transform_id: str) -> bool | None:
    for tc in profile.transforms:
        if tc.id == transform_id:
            return tc.enabled
    return None  # transform absent from profile entirely


def test_cid_aware_resample_disabled() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    enabled = _enabled_state(profile, "audio.resample")
    # Acceptable: disabled (False) OR fully removed (None).
    assert enabled is not True, (
        "audio.resample 47999↔48000 is a placebo (0.002 % shift, "
        "below chromaprint quantization). Keep it disabled."
    )


def test_cid_aggressive_resample_disabled() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aggressive.yaml")
    enabled = _enabled_state(profile, "audio.resample")
    assert enabled is not True, "Same placebo guard for cid_aggressive."
