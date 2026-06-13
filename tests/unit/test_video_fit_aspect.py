"""Snapshot tests for video.fit_aspect (v0.7 R3 / F3).

Each (aspect × mode) combination is exercised so changes to the
filter graph stand out in the diff. Pixel dimensions match the
defaults used by the shipped platform profiles in
``profiles/{youtube_*,tiktok_*,instagram_*,linkedin_*}.yaml``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator
from yt_uniquifier.core.transforms.video_fit_aspect import (
    FitAspectParams,
    _resolve_dims,
)


@pytest.fixture()
def spec():
    return get("video.fit_aspect")


# ---- resolve_dims ----
@pytest.mark.parametrize(
    "aspect,expected",
    [
        ("16:9", (1920, 1080)),
        ("9:16", (1080, 1920)),
        ("1:1",  (1080, 1080)),
        ("4:5",  (1080, 1350)),
        ("4:3",  (1440, 1080)),
    ],
)
def test_default_dims_per_aspect(aspect: str, expected: tuple[int, int]) -> None:
    p = FitAspectParams(target_aspect=aspect)
    assert _resolve_dims(p) == expected


def test_target_width_override_computes_height_from_aspect() -> None:
    p = FitAspectParams(target_aspect="9:16", target_width=720)
    w, h = _resolve_dims(p)
    assert w == 720
    # 720 × (16/9) = 1280 — matches the conventional 720×1280 vertical.
    assert h == 1280


def test_target_height_override_computes_width() -> None:
    p = FitAspectParams(target_aspect="16:9", target_height=720)
    w, h = _resolve_dims(p)
    assert h == 720
    assert w == 1280


def test_both_overrides_pass_through() -> None:
    p = FitAspectParams(
        target_aspect="1:1", target_width=512, target_height=512,
    )
    assert _resolve_dims(p) == (512, 512)


# ---- crop mode ----
def test_crop_mode_vertical(spec) -> None:
    p = FitAspectParams(target_aspect="9:16", mode="crop")
    c = spec.build(p, LabelAllocator(), "v0")
    assert c.in_label == "v0"
    assert c.out_label == "v1"
    assert c.filter_str == (
        "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=1080:1920"
    )
    assert c.extra_inputs == ()


def test_crop_mode_square(spec) -> None:
    p = FitAspectParams(target_aspect="1:1", mode="crop")
    c = spec.build(p, LabelAllocator(), "v0")
    assert c.filter_str == (
        "scale=1080:1080:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=1080:1080"
    )


def test_crop_mode_widescreen_4k(spec) -> None:
    p = FitAspectParams(
        target_aspect="16:9", mode="crop",
        target_width=3840, target_height=2160,
    )
    c = spec.build(p, LabelAllocator(), "v0")
    assert "scale=3840:2160:force_original_aspect_ratio=increase" in c.filter_str
    assert "crop=3840:2160" in c.filter_str


# ---- pad_black mode ----
def test_pad_black_default_color(spec) -> None:
    p = FitAspectParams(target_aspect="16:9", mode="pad_black")
    c = spec.build(p, LabelAllocator(), "v0")
    assert c.filter_str == (
        "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def test_pad_black_custom_color(spec) -> None:
    p = FitAspectParams(target_aspect="1:1", mode="pad_black", pad_color="0x101010")
    c = spec.build(p, LabelAllocator(), "v0")
    assert "color=0x101010" in c.filter_str


def test_pad_color_rejects_injection() -> None:
    """Same guard as RotateParams.fillcolor_*: no `;[]` allowed."""
    with pytest.raises(ValidationError):
        FitAspectParams(
            target_aspect="1:1", mode="pad_black",
            pad_color="black;scale=1:1[x];[x]",
        )


# ---- pad_blur mode ----
def test_pad_blur_uses_split_and_overlay(spec) -> None:
    p = FitAspectParams(target_aspect="9:16", mode="pad_blur", blur_sigma=15.0)
    c = spec.build(p, LabelAllocator(), "v0")
    # Multi-stage filter requiring split + overlay + __IN__ placeholder
    # (so pipeline._wrap_chain_str doesn't double-prefix the in-label).
    assert "[__IN__]split=2" in c.filter_str
    assert "gblur=sigma=15.00" in c.filter_str
    assert "overlay=(W-w)/2:(H-h)/2" in c.filter_str
    # Both scale calls present: bg uses increase (cover), fg uses decrease (fit).
    assert "force_original_aspect_ratio=increase" in c.filter_str
    assert "force_original_aspect_ratio=decrease" in c.filter_str
    # Chain still terminates with its own [out_label] (the pipeline
    # detects __IN__ and skips the default wrap entirely).
    assert c.filter_str.endswith(f"[{c.out_label}]")


def test_pad_blur_allocates_five_labels(spec) -> None:
    """One output + 4 intermediate (bg_in, fg_in, bg_out, fg_out)."""
    alloc = LabelAllocator()
    spec.build(FitAspectParams(target_aspect="9:16", mode="pad_blur"), alloc, "v0")
    # After one build, label counter should be at v5.
    assert alloc.next("v") == "v6"


# ---- param validation ----
def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        FitAspectParams(target_aspect="1:1", unknown_field=42)  # type: ignore[call-arg]


def test_unsupported_aspect_rejected() -> None:
    with pytest.raises(ValidationError):
        FitAspectParams(target_aspect="21:9")  # type: ignore[arg-type]


def test_unsupported_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        FitAspectParams(target_aspect="1:1", mode="stretch")  # type: ignore[arg-type]


def test_blur_sigma_bounds() -> None:
    with pytest.raises(ValidationError):
        FitAspectParams(target_aspect="1:1", mode="pad_blur", blur_sigma=-1.0)
    with pytest.raises(ValidationError):
        FitAspectParams(target_aspect="1:1", mode="pad_blur", blur_sigma=100.0)
