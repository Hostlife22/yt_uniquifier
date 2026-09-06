import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from tools.media_diagnostics import decoded_timeline


@pytest.mark.integration
@needs_ffmpeg
def test_exact_lossless_video_and_audio_counts(tmp_path: Path) -> None:
    media = tmp_path / "exact.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=64x64:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1",
        "-c:v", "ffv1", "-c:a", "pcm_s16le", str(media),
    ], check=True, timeout=30)
    result = decoded_timeline(media)
    video, audio = result["streams"]
    assert video["frames"] == 25
    assert audio["samples"] == 48000
    assert abs(audio["audio_minus_video_end_sec"]) < 0.001
    assert video["non_increasing_pts_frames"] == 0
    assert audio["non_increasing_pts_frames"] == 0
