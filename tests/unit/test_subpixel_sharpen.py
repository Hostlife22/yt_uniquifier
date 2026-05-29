"""video.subpixel_sharpen — unsharp luma-only for neural-FP perturbation.

Source: Singh et al. arXiv:2511.05399 (2025).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_subpixel_sharpen import SubpixelSharpenParams


def test_default_filter_shape() -> None:
    spec = get("video.subpixel_sharpen")
    chain = call_build(spec, SubpixelSharpenParams(), LabelAllocator(), "0:v:0")
    assert chain.filter_str == "unsharp=lx=5:ly=5:la=0.0500:cx=5:cy=5:ca=0.0"


def test_chroma_always_zero() -> None:
    """chroma untouched at any luma_amount — avoids color fringing."""
    spec = get("video.subpixel_sharpen")
    chain = call_build(
        spec, SubpixelSharpenParams(luma_amount=0.25),
        LabelAllocator(), "0:v:0",
    )
    assert "ca=0.0" in chain.filter_str


def test_radius_param_propagates() -> None:
    spec = get("video.subpixel_sharpen")
    chain = call_build(
        spec, SubpixelSharpenParams(radius=7),
        LabelAllocator(), "0:v:0",
    )
    assert "lx=7:ly=7" in chain.filter_str
    assert "cx=7:cy=7" in chain.filter_str


def test_even_radius_rounded_up() -> None:
    """ffmpeg unsharp requires odd kernel; 4 should become 5 at build time."""
    spec = get("video.subpixel_sharpen")
    # Even values within bounds get rounded — but the schema allows them.
    chain = call_build(
        spec, SubpixelSharpenParams(radius=4),
        LabelAllocator(), "0:v:0",
    )
    assert "lx=5:ly=5" in chain.filter_str  # 4+1=5


def test_schema_rejects_visible_amount() -> None:
    """luma_amount > 0.3 is visibly sharp — reject."""
    with pytest.raises(ValidationError):
        SubpixelSharpenParams(luma_amount=0.5)


def test_schema_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        SubpixelSharpenParams(luma_amount=-0.1)
