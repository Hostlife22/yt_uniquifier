"""Snapshot-style tests for individual transforms (deterministic inputs)."""

from __future__ import annotations

import pytest

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_pitch import (
    ATEMPO_MAX,
    ATEMPO_MIN,
    PitchTempoParams,
    cascade_atempo,
)
from yt_uniquifier.core.transforms.base import LabelAllocator
from yt_uniquifier.core.transforms.video_geom import CropResizeParams, RotateParams


def test_crop_resize_is_deterministic_with_seed() -> None:
    spec = get("video.crop_resize")
    p = CropResizeParams(max_strength=0.03, rng_seed=42)
    c1 = spec.build(p, LabelAllocator(), "0:v:0")
    c2 = spec.build(p, LabelAllocator(), "0:v:0")
    assert c1.filter_str == c2.filter_str
    assert "crop=iw*" in c1.filter_str
    assert "scale=iw/" in c1.filter_str
    assert "flags=lanczos" in c1.filter_str


def test_crop_resize_different_seed_changes_output() -> None:
    spec = get("video.crop_resize")
    a = spec.build(CropResizeParams(rng_seed=1), LabelAllocator(), "0:v:0")
    b = spec.build(CropResizeParams(rng_seed=2), LabelAllocator(), "0:v:0")
    assert a.filter_str != b.filter_str


def test_rotate_emits_radians() -> None:
    spec = get("video.rotate")
    c = spec.build(RotateParams(degrees=0.5), LabelAllocator(), "v0")
    assert "rotate=0.5*PI/180" in c.filter_str
    assert "fillcolor=black" in c.filter_str
    assert "crop=iw:ih" in c.filter_str


def test_atempo_single_factor_inside_range() -> None:
    assert cascade_atempo(0.9) == "atempo=0.900000"
    assert cascade_atempo(1.5) == "atempo=1.500000"


def test_atempo_cascade_below_min() -> None:
    out = cascade_atempo(0.3)
    assert out.startswith("atempo=")
    assert "," in out
    factors = [float(p.split("=")[1]) for p in out.split(",")]
    product = 1.0
    for f in factors:
        product *= f
        assert ATEMPO_MIN <= f <= ATEMPO_MAX
    assert abs(product - 0.3) < 1e-6


def test_atempo_cascade_above_max() -> None:
    out = cascade_atempo(3.5)
    factors = [float(p.split("=")[1]) for p in out.split(",")]
    product = 1.0
    for f in factors:
        product *= f
        assert ATEMPO_MIN <= f <= ATEMPO_MAX
    assert abs(product - 3.5) < 1e-6


def test_atempo_zero_or_negative_raises() -> None:
    with pytest.raises(ValueError):
        cascade_atempo(0.0)
    with pytest.raises(ValueError):
        cascade_atempo(-1.0)


def test_pitch_tempo_chain_shape() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.001, tempo=1.0, sample_rate=48000)
    c = spec.build(p, LabelAllocator(), "0:a:0")
    assert c.filter_str.startswith("asetrate=48000*1.001000,aresample=48000,atempo=")


def test_blend_b_has_extra_input() -> None:
    from yt_uniquifier.core.transforms.video_blend import BlendBParams

    spec = get("video.blend_b")
    c = spec.build(
        BlendBParams(b_video_path="/tmp/b.mp4", opacity=0.05),
        LabelAllocator(),
        "0:v:0",
    )
    assert c.extra_inputs == ("/tmp/b.mp4",)
    assert "scale2ref" in c.filter_str
    assert "blend=all_expr" in c.filter_str
    assert "__B__" in c.filter_str  # placeholder, rewritten by pipeline


def test_loudnorm_parse_measurement() -> None:
    from yt_uniquifier.core.transforms.audio_loudnorm import _parse_measurement

    stderr = """
[Parsed_loudnorm_0 @ 0x7f]
{
    "input_i" : "-22.50",
    "input_tp" : "-3.20",
    "input_lra" : "5.10",
    "input_thresh" : "-32.60",
    "output_i" : "-14.00",
    "output_tp" : "-1.50",
    "output_lra" : "5.00",
    "output_thresh" : "-24.10",
    "normalization_type" : "linear",
    "target_offset" : "0.04"
}

ffmpeg done.
"""
    m = _parse_measurement(stderr)
    assert m.input_i == -22.5
    assert m.input_tp == -3.2
    assert m.input_lra == 5.1
    assert m.input_thresh == -32.6
    assert m.target_offset == 0.04
