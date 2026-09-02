"""Production Phase 1 media-contract regressions using real FFmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.auxiliary_streams import get_auxiliary_streams
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


def _audio_packet_bounds(path: Path) -> tuple[float, float]:
    payload = json.loads(subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_packets", "-show_entries", "packet=pts_time,duration_time",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout)
    packets = payload["packets"]
    starts = [float(packet["pts_time"]) for packet in packets]
    ends = [
        float(packet["pts_time"]) + float(packet.get("duration_time", 0.0))
        for packet in packets
    ]
    return min(starts), max(ends)


def _assert_fully_decodable(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-map", "0:v", "-map", "0:a?", "-f", "null", "-",
        ],
        check=True, capture_output=True, timeout=60,
    )


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
            "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:a:0", "title=Main mix",
            "-disposition:a:0", "default+original",
            "-metadata:s:s:0", "language=rus",
            "-metadata:s:s:0", "title=Russian captions",
            "-disposition:s:0", "forced",
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
            "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv",
            "-c:a:0", "aac", "-c:a:1", "libopus",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:a:1", "language=spa",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@pytest.fixture
def attached_mkv_source(tmp_path: Path) -> Path:
    attachment = tmp_path / "licensed-font.txt"
    attachment.write_bytes(b"authorized attachment payload\n")
    source = tmp_path / "attached.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-attach", str(attachment),
            "-metadata:s:t:0", "filename=licensed-font.txt",
            "-metadata:s:t:0", "mimetype=text/plain",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@pytest.fixture
def timecode_mov_source(tmp_path: Path) -> Path:
    source = tmp_path / "timecode.mov"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-timecode", "01:00:00:00",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@pytest.fixture
def cover_art_mp4_source(tmp_path: Path) -> Path:
    cover = tmp_path / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:size=320x180",
            "-frames:v", "1", str(cover),
        ],
        check=True, capture_output=True, timeout=60,
    )
    source = tmp_path / "cover-source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-i", str(cover),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:v:0",
            "-c:v:0", "libx264", "-preset:v:0", "ultrafast",
            "-pix_fmt:v:0", "yuv420p", "-c:a", "aac", "-c:v:1", "copy",
            "-disposition:v:1", "attached_pic",
            "-metadata:s:v:1", "title=Cover",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    return source


@pytest.fixture
def ass_subtitle_source(tmp_path: Path) -> Path:
    subtitle = tmp_path / "captions.ass"
    subtitle.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 160\nPlayResY: 90\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,14,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.10,0:00:01.50,Default,,0,0,0,,"
        "Authorized subtitle\n",
        encoding="utf-8",
    )
    source = tmp_path / "ass-source.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-i", str(subtitle),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "ass",
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", "title=Styled captions",
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
    assert result.audio[0].title == "Main mix"
    # MP4 cannot represent Matroska's `original` disposition; preflight
    # reports the normalization and the muxer retains the representable flag.
    assert result.audio[0].dispositions == ("default",)
    assert len(result.subtitle) == 1
    assert result.subtitle[0].codec in {"mov_text", "tx3g"}
    assert result.subtitle[0].language == "rus"
    assert result.subtitle[0].title == "Russian captions"
    # MOV/MP4 marks the first subtitle default when no source subtitle was.
    assert result.subtitle[0].dispositions == ("default", "forced")
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
def test_mkv_attachment_survives_full_pipeline(
    attached_mkv_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    profile = soft.model_copy(update={"output_container": "mkv"})
    plan = build_plan(attached_mkv_source, profile, encoder_override="libx264")
    output = tmp_path / "attached-output.mkv"

    run_full(plan, RunOptions(
        work_dir=tmp_path / "work-attached", output=output,
        target_segment_sec=600,
    ))

    auxiliary = get_auxiliary_streams(probe(output))
    assert len(auxiliary) == 1
    assert auxiliary[0].kind == "attachment"
    assert auxiliary[0].filename == "licensed-font.txt"
    assert auxiliary[0].mimetype == "text/plain"
    extracted = tmp_path / "extracted.txt"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-dump_attachment:t:0", str(extracted), "-i", str(output),
            "-f", "null", "-",
        ],
        check=True, capture_output=True, timeout=60,
    )
    assert extracted.read_bytes() == b"authorized attachment payload\n"


@needs_ffmpeg
@pytest.mark.integration
def test_mov_timecode_track_survives_full_pipeline(
    timecode_mov_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    profile = soft.model_copy(update={"output_container": "mov"})
    plan = build_plan(timecode_mov_source, profile, encoder_override="libx264")
    output = tmp_path / "timecode-output.mov"

    run_full(plan, RunOptions(
        work_dir=tmp_path / "work-timecode", output=output,
        target_segment_sec=600,
    ))

    auxiliary = get_auxiliary_streams(probe(output))
    assert len(auxiliary) == 1
    assert auxiliary[0].kind == "data"
    assert auxiliary[0].codec_tag == "tmcd"
    assert auxiliary[0].timecode == "01:00:00:00"


@needs_ffmpeg
@pytest.mark.integration
def test_mp4_attached_picture_survives_full_pipeline(
    cover_art_mp4_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(cover_art_mp4_source, soft, encoder_override="libx264")
    output = tmp_path / "cover-output.mp4"

    run_full(plan, RunOptions(
        work_dir=tmp_path / "work-cover", output=output,
        target_segment_sec=600,
    ))

    result = probe(output)
    assert len(result.video) == 1
    auxiliary = get_auxiliary_streams(result)
    assert len(auxiliary) == 1
    assert auxiliary[0].kind == "attached_pic"
    assert auxiliary[0].codec == "mjpeg"
    source_cover = tmp_path / "source-cover.jpg"
    output_cover = tmp_path / "output-cover.jpg"
    for media, extracted in (
        (cover_art_mp4_source, source_cover),
        (output, output_cover),
    ):
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(media), "-map", "0:v:1", "-c", "copy",
                "-frames:v", "1", str(extracted),
            ],
            check=True, capture_output=True, timeout=60,
        )
    assert output_cover.read_bytes() == source_cover.read_bytes()


@needs_ffmpeg
@pytest.mark.integration
def test_ass_subtitle_policy_for_mkv_and_mov(
    ass_subtitle_source: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    expected_codecs = {"mkv": {"ass"}, "mov": {"mov_text", "tx3g"}}
    for container in ("mkv", "mov"):
        profile = soft.model_copy(update={
            "name": f"ass-{container}",
            "output_container": container,
        })
        plan = build_plan(ass_subtitle_source, profile, encoder_override="libx264")
        output = tmp_path / f"ass-output.{container}"
        run_full(plan, RunOptions(
            work_dir=tmp_path / f"work-ass-{container}", output=output,
            target_segment_sec=600,
        ))

        subtitle = probe(output).subtitle
        assert len(subtitle) == 1
        assert subtitle[0].codec in expected_codecs[container]
        assert subtitle[0].language == "eng"
        assert subtitle[0].title == "Styled captions"


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("container", ["mp4", "mkv", "mov"])
def test_container_roundtrip_is_decodable_and_preserves_media_contract(
    media_contract_source: Path,
    tmp_path: Path,
    isolated_cache: Path,
    container: str,
) -> None:
    soft = load_profile(PROFILES_DIR / "soft.yaml")
    profile = soft.model_copy(update={
        "name": f"container-{container}",
        "output_container": container,
    })
    plan = build_plan(media_contract_source, profile, encoder_override="libx264")
    output = tmp_path / f"roundtrip.{container}"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / f"work-{container}" / plan.plan_hash,
            output=output,
            target_segment_sec=600,
        ),
    )

    _assert_fully_decodable(output)
    result = probe(output)
    assert result.container == container
    assert result.duration_sec == pytest.approx(3.0, abs=0.05)
    assert result.video[0].duration_sec == pytest.approx(3.0, abs=0.05)
    audio_start, audio_end = _audio_packet_bounds(output)
    # AAC priming may expose one negative packet accompanied by Skip Samples;
    # decoded program content starts at zero and the tail stays within two frames.
    assert -0.05 <= audio_start <= 0.0
    assert audio_end == pytest.approx(3.0, abs=0.05)
    assert result.video[0].color.transfer == "bt709"
    assert result.video[0].color.primaries == "bt709"
    assert result.video[0].color.space == "bt709"
    assert result.video[0].color.bit_depth == 8
    assert len(result.audio) == 1
    assert len(result.subtitle) == 1
    assert [chapter.title for chapter in result.chapters] == ["Opening", "Ending"]
    if container in {"mp4", "mov"}:
        assert result.subtitle[0].codec in {"mov_text", "tx3g"}
        atoms = output.read_bytes()
        assert 0 <= atoms.find(b"moov") < atoms.find(b"mdat")
    else:
        assert result.subtitle[0].codec in {"subrip", "srt"}


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
