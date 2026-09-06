"""Audio onset must keep its source clock, not merely the final duration."""

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from tests.integration.test_production_phase1_media_contract import (
    _decoded_mono_samples,
    _loud_event_times,
)
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.orchestrator import build_plan
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_main_audio_command_windowed,
)


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("windowed", [False, True])
@pytest.mark.parametrize("rate", [1.0, 2.0])
def test_delayed_main_audio_preserves_event_clock(
    tmp_path: Path, isolated_cache: Path, windowed: bool, rate: float,
) -> None:
    source = tmp_path / "delayed.mkv"
    expression = "0.8*sin(2*PI*440*t)*(exp(-pow((t-1)/0.02,2))+exp(-pow((t-65)/0.02,2)))"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=10:duration=130",
        "-itsoffset", "1.25", "-f", "lavfi", "-i",
        f"aevalsrc='{expression}':s=48000:d=128.75",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", str(source),
    ], check=True, capture_output=True, timeout=30)
    profile = Profile(
        name="delayed-audio-contract", skip_watermark_check=True,
        seed_strategy="divergent" if windowed else "per_run", transforms=[
            TransformConfig(id="video.speed", params={"rate": rate}),
            TransformConfig(id="audio.pitch_tempo", params={"pitch": 1, "tempo": rate}),
        ],
    )
    plan = build_plan(source, profile, encoder_override="libx264")
    builder = build_main_audio_command_windowed if windowed else build_main_audio_command
    output = tmp_path / "audio.m4a"
    command, _ = builder(plan, output)
    subprocess.run(command.args, check=True, capture_output=True, timeout=60)
    samples = _decoded_mono_samples(output)
    events = _loud_event_times(samples)
    assert len(events) == 2
    assert events == pytest.approx([2.25 / rate, 66.25 / rate], abs=0.03)
