"""Real-FFmpeg smoke coverage for the stratified calibration probe."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from yt_uniquifier.core.calibration.loop import _cut_test_clip
from yt_uniquifier.core.probe import probe

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not available",
    ),
]


def _make_source(path: Path, frequency: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=9",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration=9",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_stratified_probe_is_decodable_cached_and_content_keyed(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    work = tmp_path / "work"
    _make_source(source, 440)

    first = _cut_test_clip(source, work, 3.0)
    first_stat = first.stat()
    first_meta = probe(first)
    assert first_meta.video
    assert first_meta.audio
    # AAC priming and stream-copy keyframe boundaries add a small container
    # tail; they must not turn the three-second budget into three full clips.
    assert 2.5 <= first_meta.duration_sec <= 3.5

    cached = _cut_test_clip(source, work, 3.0)
    assert cached == first
    assert cached.stat().st_mtime_ns == first_stat.st_mtime_ns

    # Same path and duration but changed head/tail content must not reuse the
    # previous representative probe.
    _make_source(source, 880)
    replaced = _cut_test_clip(source, work, 3.0)
    assert replaced != first
    assert replaced.is_file()
