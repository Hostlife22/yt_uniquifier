"""Regression: build_video_segment_command must apply HDR linear wrap.

The free-function path used by segmenter.process_video_segment previously
hand-rolled a plain for-loop over video transforms with no HDR awareness.
FilterGraph.build (full-file path) had the correct logic via
``_group_runs`` + ``_wrap_color_run``. Real production runs are segmented,
so HDR sources with ``keep_hdr=True`` and a color transform silently
produced wrong colors — operations happened on PQ-encoded values rather
than linear light.

Both call sites now route through ``_build_video_chain``.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_pipeline_hdr_grouping import _plan, _src
from yt_uniquifier.core.models import TransformConfig
from yt_uniquifier.core.pipeline import build_video_segment_command


def test_segment_command_wraps_color_transforms_for_hdr_source(
    tmp_path: Path,
) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(
        src, [TransformConfig(id="video.color_eq")], keep_hdr=True,
    )

    seg_in = tmp_path / "seg_src.mkv"
    seg_in.touch()
    built = build_video_segment_command(plan, seg_in, tmp_path / "seg_out.mkv")

    # The linear-wrap marker emitted by wrap_linear() — its presence is
    # the load-bearing invariant: color math runs in linear light, not
    # on the PQ-encoded pixel values.
    assert "zscale=transfer=linear" in built.filter_complex, (
        "HDR segment with color transform must zscale-wrap to linear "
        f"before applying it. Got: {built.filter_complex}"
    )
    # And the closing zscale back to the source transfer.
    assert "zscale=transfer=smpte2084" in built.filter_complex


def test_segment_command_no_wrap_when_keep_hdr_false(tmp_path: Path) -> None:
    """Sanity: with keep_hdr=False the HDR source is treated as SDR; no
    wrap should be applied even for color transforms."""
    src = _src(tmp_path, hdr=True)
    plan = _plan(
        src, [TransformConfig(id="video.color_eq")], keep_hdr=False,
    )

    seg_in = tmp_path / "seg_src.mkv"
    seg_in.touch()
    built = build_video_segment_command(plan, seg_in, tmp_path / "seg_out.mkv")

    assert "zscale=transfer=linear" not in built.filter_complex


def test_segment_command_no_wrap_for_sdr_source(tmp_path: Path) -> None:
    """Sanity: SDR source never needs linear wrap regardless of keep_hdr."""
    src = _src(tmp_path, hdr=False)
    plan = _plan(
        src, [TransformConfig(id="video.color_eq")], keep_hdr=True,
    )

    seg_in = tmp_path / "seg_src.mkv"
    seg_in.touch()
    built = build_video_segment_command(plan, seg_in, tmp_path / "seg_out.mkv")

    assert "zscale=transfer=linear" not in built.filter_complex
