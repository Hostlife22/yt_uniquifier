"""Snapshot-style tests for individual transforms (deterministic inputs)."""

from __future__ import annotations

from pathlib import Path

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
    assert "scale=round(iw/" in c1.filter_str
    assert "flags=lanczos" in c1.filter_str


def test_crop_resize_different_seed_changes_output() -> None:
    spec = get("video.crop_resize")
    a = spec.build(CropResizeParams(rng_seed=1), LabelAllocator(), "0:v:0")
    b = spec.build(CropResizeParams(rng_seed=2), LabelAllocator(), "0:v:0")
    assert a.filter_str != b.filter_str


def test_crop_resize_strength_is_total_per_axis() -> None:
    import re

    spec = get("video.crop_resize")
    params = CropResizeParams(max_strength=0.03, rng_seed=42)
    chain = spec.build(params, LabelAllocator(), "0:v:0")
    match = re.search(r"crop=iw\*([\d.]+):ih\*([\d.]+)", chain.filter_str)
    assert match is not None
    assert 0.97 <= float(match.group(1)) <= 1.0
    assert 0.97 <= float(match.group(2)) <= 1.0


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
    assert c.filter_str.startswith("asetrate=48048,aresample=48000,atempo=")


def test_blend_b_has_extra_input() -> None:
    from yt_uniquifier.core.transforms.video_blend import BlendBParams

    spec = get("video.blend_b")
    c = spec.build(
        BlendBParams(b_video_path="/tmp/b.mp4", opacity=0.05),
        LabelAllocator(),
        "0:v:0",
    )
    # ``BlendB.build`` normalises ``b_video_path`` via ``str(Path(...))``
    # which uses backslashes on Windows. Compare with the platform form.
    assert c.extra_inputs == (str(Path("/tmp/b.mp4")),)
    assert "scale2ref" in c.filter_str
    assert "blend=all_expr" in c.filter_str
    assert "__B__" in c.filter_str  # placeholder, rewritten by pipeline


def test_blend_b_scale2ref_ordering() -> None:
    """Regression: scale2ref must receive [__B__] BEFORE [__IN__].

    scale2ref scales its FIRST input to match the SECOND's dimensions.
    We want B scaled to A's dims, so B (the secondary input) comes
    first and A (the primary in-label) comes second. The previous form
    `[__B__]scale2ref=w=iw:h=ih` got wrapped by the pipeline as
    `[in_lbl][__B__]scale2ref…` which silently scaled A to B's
    dimensions and then blended an unchanged B at 97 % with a scaled A
    at 3 % — the inverse of the intended effect.
    """
    from yt_uniquifier.core.transforms.video_blend import (
        IN_PLACEHOLDER,
        BlendBParams,
    )

    spec = get("video.blend_b")
    c = spec.build(
        BlendBParams(b_video_path="/tmp/b.mp4", opacity=0.03),
        LabelAllocator(),
        "0:v:0",
    )
    b_idx = c.filter_str.find("[__B__]")
    in_idx = c.filter_str.find(f"[{IN_PLACEHOLDER}]")
    assert b_idx >= 0, f"missing __B__ in filter_str: {c.filter_str!r}"
    assert in_idx >= 0, f"missing __IN__ in filter_str: {c.filter_str!r}"
    assert b_idx < in_idx, (
        f"B must precede IN, got filter_str={c.filter_str!r}"
    )
    # Blend keeps the original (A) at 1 − opacity, mixes B at opacity.
    assert "A*0.9700+B*0.0300" in c.filter_str


def test_loudnorm_parse_measurement() -> None:
    from yt_uniquifier.core.transforms.audio_loudnorm import (
        _parse_measurement,
        parse_reported_normalization_mode,
    )

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
    assert parse_reported_normalization_mode(stderr) == "linear"


def test_loudnorm_reported_mode_parser_uses_last_pass() -> None:
    from yt_uniquifier.core.transforms.audio_loudnorm import (
        parse_reported_normalization_mode,
    )

    log = (
        '"normalization_type" : "linear"\n'
        'Normalization Type: Dynamic\n'
    )

    assert parse_reported_normalization_mode(log) == "dynamic"
    assert parse_reported_normalization_mode("no loudnorm report") is None
