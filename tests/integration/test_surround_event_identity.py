"""Known speaker-labelled bursts and matching flashes through the real pipeline."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from tools.media_diagnostics import compare_audio_window
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe


@pytest.mark.integration
def test_labelled_surround_bursts_retain_channels_and_flash_sync(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source, output = tmp_path / "source.mkv", tmp_path / "output.mp4"
    starts = [0.4 + index * 0.7 for index in range(6)]
    frequencies = [400, 500, 600, 80, 700, 800]  # FL FR FC LFE BL BR
    expressions = "|".join(
        f"0.2*sin(2*PI*{frequency}*t)*between(t,{start},{start + 0.2})"
        for frequency, start in zip(frequencies, starts, strict=True)
    )
    flashes = "+".join(f"between(t,{start},{start + 0.2})" for start in starts)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        f"color=black:size=64x64:rate=30:duration=5,drawbox=color=white:t=fill:enable='{flashes}'",
        "-f", "lavfi", "-i", f"aevalsrc='{expressions}':s=48000:d=5:c=5.1",
        # FLAC carries the speaker mask in MKV; raw PCM/MKV may lose that metadata.
        "-c:v", "ffv1", "-c:a", "flac", str(source),
    ], check=True, timeout=30)
    profile = Profile(name="speaker-events", skip_watermark_check=True, transforms=[
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.0004, "tempo": 1.0}),
        TransformConfig(id="audio.loudnorm"),
    ])
    plan = build_plan(source, profile, encoder_override="libx264")
    run_full(plan, RunOptions(work_dir=tmp_path / "work", output=output, target_segment_sec=2))
    assert probe(source).audio[0].channel_layout == "5.1"
    assert probe(output).audio[0].channel_layout == "5.1"
    comparison = compare_audio_window(source, output, duration=5)
    for channel in comparison["channels"]:
        assert channel["status"] == "measured"
        assert abs(channel["lag_sec"]) <= 0.03
    assert np.array(comparison["zero_lag_channel_matrix"]).argmax(axis=1).tolist() == list(range(6))
    decoded = []
    for path in (source, output):
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
            "-vf", "scale=1:1,format=gray", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-",
        ], capture_output=True, check=True, timeout=30)
        decoded.append(np.frombuffer(result.stdout, dtype=np.uint8) > 128)
    assert len(decoded[0]) == len(decoded[1]) == 150
    # All flash positions retained, not just endpoints.
    assert np.array_equal(decoded[0], decoded[1])
