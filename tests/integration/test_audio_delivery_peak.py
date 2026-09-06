"""The peak contract applies after AAC, not only at the loudnorm filter."""

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.orchestrator import build_plan
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.segmenter import process_main_audio, require_audio_delivery_peak
from yt_uniquifier.core.transforms.audio_loudnorm import measure


@pytest.mark.integration
@needs_ffmpeg
@pytest.mark.parametrize("channels", [2, 6])
def test_encoded_audio_and_cached_peak_gate(
    tmp_path: Path, isolated_cache: Path, channels: int,
) -> None:
    source = tmp_path / "transients.mkv"
    expression = "|".join(
        f"1.2*sin(2*PI*{997 + index * 13}*t)*if(lt(mod(t,0.25),0.1),1,0.01)"
        for index in range(channels)
    )
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=64x64:rate=10:duration=2",
        "-f", "lavfi", "-i", f"aevalsrc='{expression}':s=48000:d=2",
        "-c:v", "ffv1", "-c:a", "pcm_f32le", str(source),
    ], check=True, timeout=30)
    profile = Profile(
        name="peak-contract", skip_watermark_check=True,
        transforms=[TransformConfig(id="audio.loudnorm")],
    )
    plan = build_plan(source, profile, encoder_override="libx264")
    with pytest.raises(PipelineError, match="true peak"):
        require_audio_delivery_peak(plan, source)
    work = tmp_path / "work"
    work.mkdir()
    audio, _ = process_main_audio(plan, work)
    assert audio is not None
    assert probe(audio).audio[0].channels == channels
    assert measure(audio).input_tp <= -1.4
    require_audio_delivery_peak(plan, audio)
    assert "audio_delivery" in (work / "main_audio.m4a.log").read_text()
