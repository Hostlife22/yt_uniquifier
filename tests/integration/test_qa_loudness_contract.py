"""Full decoded output loudness preserves each track and the original layout."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.qa.report import build_report, render_html


@needs_ffmpeg
@pytest.mark.integration
def test_real_stereo_and_silent_surround_reports(tmp_path: Path) -> None:
    source = tmp_path / "tracks.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=3",
        "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=44100:duration=3",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=5.1:d=3",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-ac:a:0", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", str(source),
    ], check=True, capture_output=True, timeout=30)
    report = build_report(
        source, source, samples=3, run_vmaf=False, run_ssim=False,
        run_audio_fp=False, predict_cid=False, run_loudness=True,
    )
    assert report.correctness.status == "passed"
    assert report.correctness.full_decode_status == "passed"
    assert report.loudness.status == "passed"
    stereo, surround = report.loudness.streams
    assert [stereo.stream_index, surround.stream_index] == [1, 2]
    assert -30 < stereo.integrated_lufs < -10
    assert -30 < stereo.true_peak_dbtp < -10
    assert surround.integrated_lufs is None and surround.true_peak_dbtp is None
    raw = report.model_dump_json()
    assert "NaN" not in raw and "Infinity" not in raw
    assert json.loads(raw)["loudness"]["streams"][1]["true_peak_dbtp"] is None
    html = tmp_path / "report.html"
    render_html(report, None, html)
    assert "Output loudness measurement: passed" in html.read_text()
