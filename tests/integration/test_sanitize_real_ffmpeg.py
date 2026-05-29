"""Real-ffmpeg integration for v0.4.3 sanitizer."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.sanitizer import sanitize_bitstream


@needs_ffmpeg
@pytest.mark.integration
def test_sanitize_produces_h264_aac_output(tiny_clip: Path, tmp_path: Path) -> None:
    """Pre-encode with libx264 (so test runs on any CI without NVENC),
    then sanitize, verify output is still h264 + aac with chapters/streams.
    """
    pre = tmp_path / "pre.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(tiny_clip),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        str(pre),
    ], check=True, capture_output=True)

    out = tmp_path / "sanitized.mp4"
    sanitize_bitstream(pre, out)
    assert out.exists()
    assert out.stat().st_size > 0

    # Verify output codecs.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "codec_name=h264" in probe.stdout
    assert "codec_name=aac" in probe.stdout
