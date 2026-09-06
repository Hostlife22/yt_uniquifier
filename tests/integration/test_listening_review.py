import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from tools.listening_review import pcm_diagnostics, prepare_review


@pytest.mark.integration
@needs_ffmpeg
def test_review_retains_polarity_silence_and_overloads(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=64x64:rate=10:duration=1",
        "-f", "lavfi", "-i",
        "aevalsrc=1.2*sin(2*PI*440*t)|-1.2*sin(2*PI*440*t)|0:s=48000:d=1",
        "-c:v", "ffv1", "-c:a", "pcm_f32le", str(source),
    ], check=True, timeout=30)
    destination = tmp_path / "review"
    report = prepare_review(
        {"source": source}, destination, start=0, duration=1,
        rights_reference="locally generated test fixture",
    )
    clip = report["clips"][0]
    assert clip["pcm"]["nonfinite_values"] == 0
    assert clip["pcm"]["samples_at_or_above_full_scale_per_channel"][0] > 0
    assert clip["pcm"]["channel_correlations"][0]["pearson_zero_lag"] == pytest.approx(-1)
    assert clip["pcm"]["sample_peak_dbfs_per_channel"][2] is None
    assert json.loads((destination / "review.json").read_text())["listening"].startswith(
        "NOT VERIFIED"
    )
    assert pcm_diagnostics(destination / clip["file"], channels=3)["decoded_samples_per_channel"] \
        == 48000
