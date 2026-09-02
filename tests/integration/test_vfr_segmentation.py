"""Real-FFmpeg VFR regression coverage across segmented processing and concat."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import EncoderCandidate, Plan, Profile
from yt_uniquifier.core.orchestrator import RunOptions, run_full
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.probe import probe


def _make_vfr_clip(output: Path) -> None:
    """Create 6 seconds containing 30, 20, then 60 fps timestamp regions."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000:duration=6",
            "-vf",
            "select='if(lt(t,2),not(mod(n,2)),if(lt(t,4),not(mod(n,3)),1))'",
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*1)",
            "-c:a", "aac", "-shortest", str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _probe_json(path: Path, *entries: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames",
            "-show_entries", ":".join(entries), "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _video_pts(path: Path) -> list[float]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time",
            "-of", "csv=p=0", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [
        float(line.split(",", 1)[0])
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _has_delta(deltas: list[float], expected: float) -> bool:
    return any(abs(delta - expected) <= 0.002 for delta in deltas)


@needs_ffmpeg
@pytest.mark.integration
def test_segmented_vfr_preserves_frames_cadence_and_av_timeline(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / "source-vfr.mp4"
    output_path = tmp_path / "output-vfr.mp4"
    _make_vfr_clip(source_path)

    source = probe(source_path)
    assert source.video[0].fps == pytest.approx(110 / 3, rel=1e-4)
    profile = Profile(name="vfr-contract", transforms=[], skip_watermark_check=True)
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(
        source=source,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source, profile, encoder),
    )
    summary = run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )
    assert summary.segments_done >= 5

    source_pts = _video_pts(source_path)
    output_pts = _video_pts(output_path)
    assert len(source_pts) == 220
    assert len(output_pts) == len(source_pts)
    pairs = zip(output_pts, output_pts[1:], strict=False)
    deltas = [right - left for left, right in pairs]
    assert all(delta > 0 for delta in deltas)
    assert _has_delta(deltas, 1 / 60)
    assert _has_delta(deltas, 1 / 30)
    assert _has_delta(deltas, 1 / 20)

    streams = _probe_json(
        output_path,
        "stream=index,codec_type,start_time,duration,nb_read_frames",
    )["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    video_end = float(video["start_time"]) + float(video["duration"])
    audio_end = float(audio["start_time"]) + float(audio["duration"])
    assert abs(video_end - 6.0) <= 0.02
    assert abs(audio_end - video_end) <= 0.05
