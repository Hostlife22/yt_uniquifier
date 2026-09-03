"""Real-FFmpeg audio transform topology matrix."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import EncoderCandidate, Plan, Profile, TransformConfig
from yt_uniquifier.core.pipeline import build_main_audio_command, compute_plan_hash
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_LAYOUTS = [("mono", 1), ("stereo", 2), ("5.1", 6)]
_LAYOUT_SAFE_TRANSFORMS = [
    "audio.eq",
    "audio.compand",
    "audio.spectral_smear",
    "audio.reverb",
    "audio.noise_overlay",
    "audio.pitch_tempo",
    "audio.resample",
]


@pytest.fixture(scope="module", params=_LAYOUTS, ids=lambda item: item[0])
def audio_layout_source(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, int]:
    layout, channels = request.param
    root = tmp_path_factory.mktemp(f"audio-{layout}")
    source = root / "source.mkv"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24:duration=1",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl={layout}", "-t", "1",
            "-c:v", "ffv1", "-c:a", "pcm_s16le", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return source, channels


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("transform_id", _LAYOUT_SAFE_TRANSFORMS)
def test_audio_transform_preserves_layout_and_output_rate(
    audio_layout_source: tuple[Path, int],
    tmp_path: Path,
    transform_id: str,
) -> None:
    source, expected_channels = audio_layout_source
    source_meta = probe(source)
    profile = Profile(
        name="audio-layout-matrix",
        transforms=[TransformConfig(id=transform_id)],
    )
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(
        source=source_meta,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source_meta, profile, encoder),
    )
    output = tmp_path / f"{transform_id.rsplit('.', 1)[1]}.m4a"
    command, _measurement = build_main_audio_command(plan, output)

    subprocess.run(command.args, check=True, capture_output=True, timeout=30)

    output_meta = probe(output)
    assert len(output_meta.audio) == 1
    assert output_meta.audio[0].channels == expected_channels
    assert output_meta.audio[0].sample_rate == 48_000


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("sample_rate", [44_100, 48_000, 96_000])
def test_pitch_pipeline_accepts_supported_source_sample_rates(
    tmp_path: Path,
    sample_rate: int,
) -> None:
    source = tmp_path / f"source-{sample_rate}.mkv"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration=1",
            "-c:a", "pcm_s16le", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    source_meta = probe(source)
    profile = Profile(
        name="sample-rate-matrix",
        transforms=[TransformConfig(
            id="audio.pitch_tempo",
            params={"pitch": 1.01, "tempo": 1.0, "method": "asetrate"},
        )],
    )
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(
        source=source_meta,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source_meta, profile, encoder),
    )
    output = tmp_path / "output.m4a"
    command, _measurement = build_main_audio_command(plan, output)

    subprocess.run(command.args, check=True, capture_output=True, timeout=30)

    output_meta = probe(output)
    assert output_meta.audio[0].sample_rate == 48_000
    assert output_meta.duration_sec == pytest.approx(1.0, abs=0.05)
