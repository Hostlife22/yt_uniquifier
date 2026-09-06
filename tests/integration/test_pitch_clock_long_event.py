"""A late event detects clock drift that final audio pad/trim would hide."""

import subprocess

import numpy as np
import pytest

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_pitch import PitchTempoParams
from yt_uniquifier.core.transforms.base import LabelAllocator


@pytest.mark.integration
def test_fractional_clock_late_event_against_legacy_compensation() -> None:
    chain = get("audio.pitch_tempo").build(
        PitchTempoParams(sample_rate=8000, pitch=1.00006), LabelAllocator(), "0:a:0",
    ).filter_str
    legacy = "asetrate=8000*1.000060,aresample=8000,atempo=0.999940"
    peaks = []
    for filters in (legacy, chain):
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "aevalsrc='0.7*sin(2*PI*300*t)*exp(-pow((t-1790)/0.01,2))':s=8000:d=1800",
            "-af", filters + ",atrim=start=1789:duration=2,asetpts=N/SR/TB",
            "-c:a", "pcm_f32le", "-f", "f32le", "-",
        ], check=True, capture_output=True, timeout=120)
        samples = np.frombuffer(result.stdout, dtype="<f4")
        assert len(samples) > 8000
        peaks.append(1789 + int(np.argmax(np.abs(samples))) / 8000)
    assert abs(peaks[0] - 1790) > 0.02  # Control reproduces the old cumulative clock error.
    assert abs(peaks[1] - 1790) < 0.005
