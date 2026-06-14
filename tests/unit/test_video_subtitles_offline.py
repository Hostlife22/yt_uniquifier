"""Snapshot + validation tests for video.subtitles (v0.9.0 R2 / F14).

Two surfaces under test:

  * The ``SubtitleBurnParams`` schema — its security guards (path
    traversal, filter-arg-injection chars, font/colour sanity).
  * The builder's emitted filter chain — exact string for two
    positions, to make any regression visible in the diff.

The preflight check is exercised separately in
``test_preflight_subtitle_burnin.py`` to keep concerns split.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator
from yt_uniquifier.core.transforms.video_subtitles import (
    SubtitleBurnParams,
    _escape_subtitle_path,
)


@pytest.fixture()
def spec():
    return get("video.subtitles")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_params_default_values_are_safe() -> None:
    p = SubtitleBurnParams(subtitle_path="captions.srt")
    assert p.font_size == 24
    assert p.position == "bottom"
    assert p.font_name == "Helvetica"


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SubtitleBurnParams.model_validate({
            "subtitle_path": "c.srt",
            "unknown": "boom",
        })


@pytest.mark.parametrize("bad", [
    "captions;rm -rf /.srt",
    "ev[il].srt",
    "ok\nrm.srt",
])
def test_subtitle_path_rejects_filter_meta(bad: str) -> None:
    with pytest.raises(ValidationError):
        SubtitleBurnParams(subtitle_path=bad)


def test_subtitle_path_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        SubtitleBurnParams(subtitle_path="../../etc/passwd")


def test_font_name_rejects_comma() -> None:
    with pytest.raises(ValidationError):
        SubtitleBurnParams(
            subtitle_path="c.srt",
            font_name="Helvetica,Arial",
        )


def test_primary_color_must_be_ass_hex() -> None:
    with pytest.raises(ValidationError):
        SubtitleBurnParams(
            subtitle_path="c.srt",
            primary_color="red",
        )


# ---------------------------------------------------------------------------
# Builder snapshot
# ---------------------------------------------------------------------------


def test_builder_emits_bottom_filter(spec) -> None:
    alloc = LabelAllocator()
    chain = spec.build(
        SubtitleBurnParams(subtitle_path="captions.srt"),
        alloc,
        "vbase",
    )
    # bottom = libass alignment 2
    assert chain.in_label == "vbase"
    assert chain.out_label == "v1"
    assert chain.filter_str == (
        "subtitles=captions.srt:force_style="
        "'FontName=Helvetica,FontSize=24,PrimaryColour=&H00FFFFFF,"
        "Outline=2,Alignment=2,MarginV=40'"
    )


def test_builder_emits_top_filter() -> None:
    alloc = LabelAllocator()
    chain = get("video.subtitles").build(
        SubtitleBurnParams(
            subtitle_path="captions.srt",
            position="top",
            font_size=18,
            margin_v=20,
        ),
        alloc,
        "in",
    )
    assert "Alignment=8" in chain.filter_str
    assert "FontSize=18" in chain.filter_str
    assert "MarginV=20" in chain.filter_str


def test_builder_escapes_special_path_chars() -> None:
    """Colons in paths (Windows drive letters, Linux absolute paths) must
    be escaped — bare ``:`` ends the filter argument."""
    alloc = LabelAllocator()
    chain = get("video.subtitles").build(
        SubtitleBurnParams(subtitle_path="/abs/path to/file.srt"),
        alloc,
        "in",
    )
    # The colon between path components is unaffected here (no colon
    # in this path); confirm the raw path appears unmodified.
    assert "/abs/path to/file.srt" in chain.filter_str

    chain2 = get("video.subtitles").build(
        SubtitleBurnParams(subtitle_path="C\\Users\\me\\caps.srt"),
        alloc,
        "in",
    )
    # Backslashes get doubled per ffmpeg's filter-arg escape rules.
    assert "C\\\\Users\\\\me\\\\caps.srt" in chain2.filter_str


def test_escape_helper_handles_quote_and_colon() -> None:
    assert _escape_subtitle_path("path:to/it's.srt") == r"path\:to/it\'s.srt"


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_video_subtitles_registered_under_canonical_id() -> None:
    spec = get("video.subtitles")
    assert spec.kind == "video"
    assert spec.schema is SubtitleBurnParams
