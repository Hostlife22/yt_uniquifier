"""Real-FFmpeg regressions for timestamp-based temporal jitter."""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_temporal_jitter import TemporalJitterParams
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin


def _decoded_frames(path: Path) -> int:
    value = subprocess.check_output(
        [
            ffprobe_bin(), "-v", "error", "-count_frames",
            "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
        timeout=30,
    ).strip()
    return int(value)


def _video_pts(path: Path) -> list[float]:
    value = subprocess.check_output(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time",
            "-of", "csv=p=0", str(path),
        ],
        text=True,
        timeout=30,
    )
    return [float(line.split(",", 1)[0]) for line in value.splitlines() if line.strip()]


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("fps", [24, 30, 60])
def test_temporal_jitter_uses_one_duration_at_different_frame_rates(
    tmp_path: Path,
    fps: int,
) -> None:
    source = tmp_path / f"source-{fps}.mp4"
    output = tmp_path / f"output-{fps}.mkv"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size=96x64:rate={fps}:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    chain = call_build(
        get("video.temporal_jitter"),
        TemporalJitterParams(blackout_prob=0.2, drop_prob=0.2),
        LabelAllocator(),
        "0:v:0",
        rng=random.Random(0),
    )
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-filter_complex", f"[0:v:0]{chain.filter_str}[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-fps_mode", "passthrough",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    source_frames = _decoded_frames(source)
    output_frames = _decoded_frames(output)
    retained = output_frames / source_frames
    assert 0.7 <= retained <= 0.9


@needs_ffmpeg
@pytest.mark.integration
def test_temporal_jitter_preserves_vfr_cadence_classes(tmp_path: Path) -> None:
    source = tmp_path / "source-vfr.mkv"
    output = tmp_path / "output-vfr.mkv"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=60:duration=6",
            "-vf",
            "select='if(lt(t,2),not(mod(n,2)),if(lt(t,4),not(mod(n,3)),1))'",
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    chain = call_build(
        get("video.temporal_jitter"),
        TemporalJitterParams(blackout_prob=0.0, drop_prob=0.2),
        LabelAllocator(),
        "0:v:0",
        rng=random.Random(0),
    )
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-filter_complex", f"[0:v:0]{chain.filter_str}[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-fps_mode", "passthrough",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    source_pts = _video_pts(source)
    output_pts = _video_pts(output)
    retained = len(output_pts) / len(source_pts)
    deltas = [
        later - earlier
        for earlier, later in zip(output_pts, output_pts[1:], strict=False)
    ]
    assert 0.7 <= retained <= 0.9
    assert any(abs(delta - 1 / 30) <= 0.002 for delta in deltas)
    assert any(abs(delta - 1 / 20) <= 0.002 for delta in deltas)
    assert any(abs(delta - 1 / 60) <= 0.002 for delta in deltas)
