"""Regression guard: cid_aware/cid_aggressive pitch must stay above the
Smitelli (2010) documented Content ID ±5% match-threshold.

Source: Scott Smitelli, "Fun with YouTube's Audio Content ID System" (2010).
Pitch shifts within ±5% match; ≥ ±6% do not. v0.3.1 shipped 1.04, which
sat inside the match zone. v0.3.2 bumps to 1.06 / 1.08 respectively.
"""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parent.parent.parent / "src" / "yt_uniquifier" / "profiles"


def _get_pitch_param(profile, transform_id: str) -> float:
    for cfg in profile.transforms:
        if cfg.id == transform_id:
            return float(cfg.params["pitch"])
    raise AssertionError(f"{transform_id} not found in profile")


def test_cid_aware_pitch_above_smitelli_threshold() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    pitch = _get_pitch_param(profile, "audio.pitch_tempo")
    # ≥1.06 = past +5% match boundary; lower bound after randomize_within
    # 0.005 jitter is 1.055, still on the no-match side.
    assert pitch >= 1.06, (
        f"cid_aware.pitch={pitch} is at or below Smitelli +5% threshold; "
        f"CID will match audio fingerprint."
    )


def test_cid_aggressive_pitch_safe_margin() -> None:
    profile = load_profile(PROFILES_DIR / "cid_aggressive.yaml")
    pitch = _get_pitch_param(profile, "audio.pitch_tempo")
    assert pitch >= 1.08, (
        f"cid_aggressive.pitch={pitch} is below the safe-margin threshold."
    )
