"""Unit tests for calibration.intensity.scale_profile."""

from __future__ import annotations

import pytest

from yt_uniquifier.core.calibration.intensity import _around_one, scale_profile
from yt_uniquifier.core.models import Profile, TransformConfig


def _profile(transforms: list[TransformConfig]) -> Profile:
    return Profile(name="t", transforms=transforms)


def _get(p: Profile, transform_id: str) -> dict:
    for tc in p.transforms:
        if tc.id == transform_id:
            # Recover the *effective* params (merge defaults).
            from yt_uniquifier.core.transforms import get
            spec = get(transform_id)
            return {**spec.defaults, **tc.params}
    raise KeyError(transform_id)


def test_around_one_helper() -> None:
    assert _around_one(1.0, 2.0) == 1.0
    assert _around_one(1.02, 2.0) == pytest.approx(1.04)
    assert _around_one(0.99, 2.0) == pytest.approx(0.98)
    assert _around_one(1.05, 0.5) == pytest.approx(1.025)


def test_scale_crop_resize() -> None:
    p = _profile([TransformConfig(id="video.crop_resize",
                                  params={"max_strength": 0.04})])
    out = scale_profile(p, 2.0)
    assert _get(out, "video.crop_resize")["max_strength"] == pytest.approx(0.08)


def test_scale_color_eq_brightness_and_around_one() -> None:
    p = _profile([TransformConfig(id="video.color_eq",
                                  params={
                                      "brightness": 0.01, "contrast": 1.02,
                                      "gamma": 0.99, "saturation": 1.03,
                                  })])
    out = scale_profile(p, 2.0)
    e = _get(out, "video.color_eq")
    assert e["brightness"] == pytest.approx(0.02)         # *factor
    assert e["contrast"] == pytest.approx(1.04)            # around-1
    assert e["gamma"] == pytest.approx(0.98)               # around-1
    assert e["saturation"] == pytest.approx(1.06)          # around-1


def test_scale_noise_strength_is_int() -> None:
    p = _profile([TransformConfig(id="video.noise", params={"strength": 4})])
    out = scale_profile(p, 1.75)
    assert _get(out, "video.noise")["strength"] == 7


def test_scale_pitch_tempo() -> None:
    p = _profile([TransformConfig(id="audio.pitch_tempo",
                                  params={
                                      "pitch": 1.012, "tempo": 1.0,
                                      "randomize_within": 0.003,
                                  })])
    out = scale_profile(p, 2.0)
    e = _get(out, "audio.pitch_tempo")
    assert e["pitch"] == pytest.approx(1.024)              # around-1
    assert e["tempo"] == pytest.approx(1.0)                # 1 stays 1
    assert e["randomize_within"] == pytest.approx(0.006)   # *factor


def test_scale_audio_eq_band_gain_only() -> None:
    p = _profile([TransformConfig(id="audio.eq",
                                  params={"bands": [[120.0, -0.6], [4500.0, 0.4]]})])
    out = scale_profile(p, 2.0)
    bands = _get(out, "audio.eq")["bands"]
    assert bands[0][0] == 120.0  # freq unchanged
    assert bands[0][1] == pytest.approx(-1.2)
    assert bands[1][1] == pytest.approx(0.8)


def test_scale_resample_delta_from_target() -> None:
    p = _profile([TransformConfig(id="audio.resample",
                                  params={"intermediate_sr": 47999, "target_sr": 48000})])
    out = scale_profile(p, 3.0)
    # delta -1, * 3 → -3 → intermediate_sr 47997
    assert _get(out, "audio.resample")["intermediate_sr"] == 47997


def test_scale_spectral_smear_intensity() -> None:
    p = _profile([TransformConfig(id="audio.spectral_smear",
                                  params={"intensity": 0.02})])
    out = scale_profile(p, 2.5)
    assert _get(out, "audio.spectral_smear")["intensity"] == pytest.approx(0.05)


def test_scale_clamps_to_field_bounds() -> None:
    # max_strength has le=0.10
    p = _profile([TransformConfig(id="video.crop_resize",
                                  params={"max_strength": 0.06})])
    out = scale_profile(p, 5.0)   # 0.30 → clamp 0.10
    assert _get(out, "video.crop_resize")["max_strength"] == pytest.approx(0.10)


def test_scale_factor_one_is_identity() -> None:
    p = _profile([
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.012}),
        TransformConfig(id="audio.eq", params={"bands": [[120.0, -0.6]]}),
    ])
    out = scale_profile(p, 1.0)
    assert _get(out, "video.crop_resize")["max_strength"] == pytest.approx(0.04)
    assert _get(out, "audio.pitch_tempo")["pitch"] == pytest.approx(1.012)
    assert _get(out, "audio.eq")["bands"][0][1] == pytest.approx(-0.6)


def test_scale_loudnorm_unchanged() -> None:
    p = _profile([TransformConfig(id="audio.loudnorm")])
    out = scale_profile(p, 5.0)
    e = _get(out, "audio.loudnorm")
    assert e["integrated"] == -14.0  # target loudness doesn't scale
