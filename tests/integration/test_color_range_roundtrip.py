"""Real-FFmpeg SDR full/limited range preservation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.pipeline import build_video_segment_command_fused
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.segmenter import plan_segments
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize(
    ("color_range", "source_filter", "x264_range"),
    [
        ("tv", "format=yuv420p", "limited"),
        ("pc", "scale=in_range=tv:out_range=pc,format=yuv420p", "full"),
    ],
    ids=["limited", "full"],
)
def test_sdr_range_survives_segment_encode_and_concat(
    tmp_path: Path,
    isolated_cache: Path,
    color_range: str,
    source_filter: str,
    x264_range: str,
) -> None:
    source = tmp_path / f"source-{color_range}.mkv"
    output = tmp_path / f"output-{color_range}.mkv"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=1",
            "-vf", source_filter,
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", color_range,
            "-c:v", "ffv1", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    profile = Profile(
        name="sdr-range-roundtrip",
        transforms=[],
        output_container="mkv",
        target_codec="h264",
    )
    plan = build_plan(source, profile, encoder_override="libx264")
    segment = plan_segments(plan, target_size_sec=600.0)[0]
    built = build_video_segment_command_fused(
        plan,
        segment,
        source,
        tmp_path / "command-check.mkv",
    )

    assert built.args[built.args.index("-color_range") + 1] == color_range
    x264_params = built.args[built.args.index("-x264-params") + 1]
    assert f"range={x264_range}" in x264_params

    summary = run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work",
            output=output,
            target_segment_sec=600.0,
            enforce_preflight=False,
        ),
    )

    assert summary.segments_done == 1
    assert probe(source).video[0].color.color_range == color_range
    assert probe(output).video[0].color.color_range == color_range
