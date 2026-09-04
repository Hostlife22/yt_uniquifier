"""Real-FFmpeg verification of the qualified libx264 upload structure."""

from __future__ import annotations

import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full


@needs_ffmpeg
@pytest.mark.integration
def test_libx264_output_has_bounded_closed_gop_and_cabac(
    tiny_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    profile = Profile(
        name="h264-upload-structure",
        transforms=[],
        output_container="mp4",
        target_codec="h264",
        skip_watermark_check=True,
    )
    plan = build_plan(tiny_clip, profile, encoder_override="libx264")
    output = tmp_path / "h264-upload-structure.mp4"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work",
            output=output,
            target_segment_sec=600.0,
        ),
    )

    stream_payload = json.loads(subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=profile,has_b_frames,r_frame_rate",
            "-show_entries", "frame=key_frame,pict_type",
            "-of", "json", str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout)
    stream = stream_payload["streams"][0]
    frames = stream_payload["frames"]
    assert stream["profile"] == "High"
    assert stream["r_frame_rate"] == "24/1"
    assert stream["has_b_frames"] > 0

    keyframe_indices = [
        index for index, frame in enumerate(frames) if frame["key_frame"] == 1
    ]
    assert keyframe_indices[0] == 0
    assert len(keyframe_indices) >= 4
    assert max(
        right - left
        for left, right in pairwise(keyframe_indices)
    ) <= 12

    longest_b_run = current_b_run = 0
    for frame in frames:
        if frame["pict_type"] == "B":
            current_b_run += 1
            longest_b_run = max(longest_b_run, current_b_run)
        else:
            current_b_run = 0
    assert longest_b_run == 2

    headers = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(output),
            "-map", "0:v:0", "-c", "copy", "-bsf:v", "trace_headers",
            "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stderr
    assert re.search(r"entropy_coding_mode_flag\s+1 = 1", headers)
    idr_count = len(re.findall(r"nal_unit_type\s+00101 = 5", headers))
    assert idr_count == len(keyframe_indices)
