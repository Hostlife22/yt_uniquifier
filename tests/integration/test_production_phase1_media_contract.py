"""Production Phase 1 media-contract regressions using real FFmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.pipeline import build_main_audio_command_windowed
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormParams, measure

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


def _audio_duration(path: Path) -> float:
    value = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=duration", "-of", "default=nw=1:nk=1",
            str(path),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return float(value)


@pytest.fixture
def media_contract_source(tmp_path: Path) -> Path:
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text(
        "1\n00:00:00,100 --> 00:00:01,200\nAuthorized caption\n",
        encoding="utf-8",
    )
    chapters = tmp_path / "chapters.ffmeta"
    chapters.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Opening\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Ending\n",
        encoding="utf-8",
    )
    source = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
            "-i", str(subtitle), "-f", "ffmetadata", "-i", str(chapters),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
            "-map_metadata", "3", "-map_chapters", "3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:s:0", "language=rus",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@pytest.fixture
def multi_audio_source(tmp_path: Path) -> Path:
    source = tmp_path / "multi-audio.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a:0", "aac", "-c:a:1", "libopus",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:a:1", "language=spa",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@needs_ffmpeg
@pytest.mark.integration
def test_no_audio_transform_preserves_main_audio_subtitles_and_chapters(
    media_contract_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    video_only = Profile.model_validate({
        **soft.model_dump(),
        "name": "video-only-contract",
        "transforms": [
            transform.model_dump()
            for transform in soft.transforms
            if not transform.id.startswith("audio.")
        ],
    })
    plan = build_plan(media_contract_source, video_only, encoder_override="libx264")
    output = tmp_path / "video-only.mp4"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-video-only" / plan.plan_hash,
            output=output,
            target_segment_sec=600,
        ),
    )

    result = probe(output)
    assert len(result.audio) == 1
    assert result.audio[0].language == "eng"
    assert len(result.subtitle) == 1
    assert result.subtitle[0].codec in {"mov_text", "tx3g"}
    assert result.subtitle[0].language == "rus"
    assert [chapter.title for chapter in result.chapters] == ["Opening", "Ending"]


@needs_ffmpeg
@pytest.mark.integration
def test_44100_pitch_pipeline_preserves_duration_and_outputs_48000(
    media_contract_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(media_contract_source, profile, encoder_override="libx264")
    output = tmp_path / "soft.mp4"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-soft" / plan.plan_hash,
            output=output,
            target_segment_sec=600,
        ),
    )

    result = probe(output)
    assert result.audio[0].sample_rate == 48_000
    assert abs(_audio_duration(output) - 3.0) <= 0.05
    loudness = measure(output, LoudnormParams(integrated=-14.0))
    assert loudness.input_i == pytest.approx(-14.0, abs=0.5)
    assert len(result.subtitle) == 1
    assert len(result.chapters) == 2


@needs_ffmpeg
@pytest.mark.integration
def test_all_audio_tracks_use_container_compatible_codecs(
    multi_audio_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    profile = soft.model_copy(update={"audio_tracks": "all", "name": "all-audio"})
    plan = build_plan(multi_audio_source, profile, encoder_override="libx264")
    output = tmp_path / "all-audio.mp4"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-all-audio" / plan.plan_hash,
            output=output,
        ),
    )

    result = probe(output)
    assert [stream.codec for stream in result.audio] == ["aac", "aac"]
    assert [stream.language for stream in result.audio] == ["eng", "spa"]


@needs_ffmpeg
@pytest.mark.integration
def test_windowed_audio_does_not_accumulate_crossfade_duration(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source = tmp_path / "long-audio-source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=size=160x90:rate=1:duration=125",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=125",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    profile = Profile(
        name="window-duration-contract",
        seed=7,
        seed_strategy="divergent",
        transforms=[TransformConfig(id="audio.eq")],
    )
    plan = build_plan(source, profile, encoder_override="libx264")
    output = tmp_path / "windowed.m4a"

    command, _ = build_main_audio_command_windowed(plan, output)
    subprocess.run(command.args, check=True, capture_output=True, timeout=60)

    # AAC uses 1024-sample frames: permit one encoded frame plus ffprobe
    # rounding, but no 0.1 s accumulation per window boundary.
    assert abs(_audio_duration(output) - 125.0) <= 0.03
