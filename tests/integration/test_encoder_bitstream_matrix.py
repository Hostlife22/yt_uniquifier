"""Real bitstream qualification for locally available delivery encoders."""

from __future__ import annotations

import json
import os
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.errors import EncoderError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

_HARDWARE_ENCODERS: dict[str, tuple[str, str, str]] = {
    "h264_nvenc": ("h264", "High", "avc1"),
    "hevc_nvenc": ("hevc", "Main", "hev1"),
    "av1_nvenc": ("av1", "Main", "av01"),
    "h264_qsv": ("h264", "High", "avc1"),
    "hevc_qsv": ("hevc", "Main", "hev1"),
    "av1_qsv": ("av1", "Main", "av01"),
    "h264_amf": ("h264", "High", "avc1"),
    "hevc_amf": ("hevc", "Main", "hev1"),
    "av1_amf": ("av1", "Main", "av01"),
    "h264_videotoolbox": ("h264", "High", "avc1"),
    "hevc_videotoolbox": ("hevc", "Main", "hev1"),
    "av1_videotoolbox": ("av1", "Main", "av01"),
}


def _requested_hardware_encoders() -> frozenset[str]:
    return frozenset(
        value.strip()
        for value in os.environ.get("YT_UNIQ_HARDWARE_ENCODERS", "").split(",")
        if value.strip()
    )


_REQUESTED_HARDWARE_ENCODERS = _requested_hardware_encoders()


def _encoder_is_listed(name: str) -> bool:
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0 and name in proc.stdout


@pytest.fixture
def bitstream_source(tmp_path: Path) -> Path:
    output = tmp_path / "bitstream-source.mp4"
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-color_range", "tv", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-colorspace", "bt709",
            "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
            "-c:a", "aac", "-shortest", str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return output


def _encode(
    source: Path,
    tmp_path: Path,
    *,
    encoder: str,
    codec: str,
    required: bool = False,
) -> Path:
    if not _encoder_is_listed(encoder):
        if required:
            pytest.fail(f"requested hardware encoder {encoder!r} is not listed by FFmpeg")
        pytest.skip(f"{encoder} is not listed by this FFmpeg build")
    profile = Profile(
        name=f"bitstream-{encoder}",
        transforms=[],
        target_codec=codec,  # type: ignore[arg-type]
        output_container="mp4",
        skip_watermark_check=True,
    )
    try:
        plan = build_plan(source, profile, encoder_override=encoder)
    except EncoderError as exc:
        if encoder.endswith("_videotoolbox") and not required:
            pytest.skip(f"{encoder} has no usable hardware session: {exc}")
        raise
    output = tmp_path / f"{encoder}.mp4"
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / f"work-{encoder}" / plan.plan_hash,
            output=output,
            target_segment_sec=600.0,
        ),
    )
    return output


def _probe_frames(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,codec_tag_string,profile,level,has_b_frames,pix_fmt,"
            "color_range,color_space,color_transfer,color_primaries,r_frame_rate",
            "-show_entries", "frame=key_frame,pict_type",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout)
    return payload["streams"][0], payload["frames"]


def _keyframes(frames: list[dict[str, object]]) -> list[int]:
    return [index for index, frame in enumerate(frames) if frame["key_frame"] == 1]


def _assert_common_delivery_contract(
    stream: dict[str, object],
    frames: list[dict[str, object]],
    *,
    codec: str,
) -> list[int]:
    assert stream["codec_name"] == codec
    assert stream["r_frame_rate"] == "24/1"
    assert stream["color_range"] == "tv"
    assert stream["color_space"] == "bt709"
    assert stream["color_transfer"] == "bt709"
    assert stream["color_primaries"] == "bt709"
    assert isinstance(stream["level"], int) and stream["level"] > 0
    assert len(frames) == 144
    keyframes = _keyframes(frames)
    assert keyframes[0] == 0
    return keyframes


def _assert_h264_structure(
    path: Path,
    stream: dict[str, object],
    frames: list[dict[str, object]],
    *,
    max_consecutive_b_frames: int = 2,
) -> None:
    keyframes = _assert_common_delivery_contract(stream, frames, codec="h264")

    assert stream["profile"] == "High"
    assert stream["codec_tag_string"] == "avc1"
    assert stream["pix_fmt"] == "yuv420p"
    assert len(keyframes) >= 12
    assert max(right - left for left, right in pairwise(keyframes)) <= 12

    longest_b_run = current_b_run = 0
    for frame in frames:
        if frame["pict_type"] == "B":
            current_b_run += 1
            longest_b_run = max(longest_b_run, current_b_run)
        else:
            current_b_run = 0
    assert 1 <= longest_b_run <= max_consecutive_b_frames

    headers = subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-map", "0:v:0", "-c", "copy", "-bsf:v", "trace_headers",
            "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stderr
    assert re.search(r"entropy_coding_mode_flag\s+1 = 1", headers)
    assert len(re.findall(r"nal_unit_type\s+00101 = 5", headers)) == len(keyframes)


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize(
    ("encoder", "codec", "profile", "tag"),
    [
        ("libx265", "hevc", "Main", "hev1"),
        ("hevc_videotoolbox", "hevc", "Main", "hev1"),
        ("libsvtav1", "av1", "Main", "av01"),
        ("libaom-av1", "av1", "Main", "av01"),
    ],
)
def test_hevc_av1_bitstream_matrix(
    bitstream_source: Path,
    tmp_path: Path,
    isolated_cache: Path,
    encoder: str,
    codec: str,
    profile: str,
    tag: str,
) -> None:
    output = _encode(
        bitstream_source,
        tmp_path,
        encoder=encoder,
        codec=codec,
    )
    stream, frames = _probe_frames(output)
    keyframes = _assert_common_delivery_contract(stream, frames, codec=codec)

    assert stream["profile"] == profile
    assert stream["codec_tag_string"] == tag
    assert stream["pix_fmt"] == "yuv420p"
    assert len(keyframes) >= 3
    assert max(right - left for left, right in pairwise(keyframes)) <= 48

    if codec == "hevc":
        headers = subprocess.run(
            [
                ffmpeg_bin(), "-hide_banner", "-loglevel", "info", "-i", str(output),
                "-map", "0:v:0", "-c", "copy", "-bsf:v", "trace_headers",
                "-f", "null", "-",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stderr
        idr_count = len(re.findall(r"nal_unit_type\s+01(?:0011|0100) = (?:19|20)", headers))
        assert idr_count == len(keyframes)


@needs_ffmpeg
@pytest.mark.integration
def test_h264_videotoolbox_bitstream_contract(
    bitstream_source: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    output = _encode(
        bitstream_source,
        tmp_path,
        encoder="h264_videotoolbox",
        codec="h264",
    )
    stream, frames = _probe_frames(output)
    # FFmpeg's VideoToolbox wrapper only maps positive ``-bf`` to Apple's
    # AllowFrameReordering boolean; the device chooses the actual pattern.
    # Qualified Intel hardware emits one consecutive B-frame, while GitHub's
    # Apple Silicon runners emit three. Both retain the requested closed IDR
    # cadence, High profile and CABAC contract checked below.
    _assert_h264_structure(output, stream, frames, max_consecutive_b_frames=3)


@needs_ffmpeg
@pytest.mark.integration
def test_requested_hardware_encoder_names_are_supported() -> None:
    if "YT_UNIQ_HARDWARE_ENCODERS" not in os.environ:
        pytest.skip("YT_UNIQ_HARDWARE_ENCODERS is not set")
    assert _REQUESTED_HARDWARE_ENCODERS, (
        "YT_UNIQ_HARDWARE_ENCODERS must contain at least one encoder name"
    )

    unknown = _REQUESTED_HARDWARE_ENCODERS.difference(_HARDWARE_ENCODERS)
    assert not unknown, (
        "unknown YT_UNIQ_HARDWARE_ENCODERS value(s): "
        f"{', '.join(sorted(unknown))}; supported: {', '.join(_HARDWARE_ENCODERS)}"
    )


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize(
    "encoder",
    [
        pytest.param(
            encoder,
            marks=pytest.mark.skipif(
                encoder not in _REQUESTED_HARDWARE_ENCODERS,
                reason=f"{encoder} was not requested for this hardware runner",
            ),
        )
        for encoder in _HARDWARE_ENCODERS
    ],
)
def test_requested_hardware_bitstream_contract(
    bitstream_source: Path,
    tmp_path: Path,
    isolated_cache: Path,
    encoder: str,
) -> None:
    codec, expected_profile, expected_tag = _HARDWARE_ENCODERS[encoder]
    output = _encode(
        bitstream_source,
        tmp_path,
        encoder=encoder,
        codec=codec,
        required=True,
    )
    stream, frames = _probe_frames(output)
    if codec == "h264":
        max_b_frames = 3 if encoder == "h264_videotoolbox" else 2
        _assert_h264_structure(
            output,
            stream,
            frames,
            max_consecutive_b_frames=max_b_frames,
        )
        return

    keyframes = _assert_common_delivery_contract(stream, frames, codec=codec)
    assert stream["profile"] == expected_profile
    assert stream["codec_tag_string"] == expected_tag
    assert stream["pix_fmt"] == "yuv420p"
    assert len(keyframes) >= 3
    assert max(right - left for left, right in pairwise(keyframes)) <= 48

    if codec == "hevc":
        headers = subprocess.run(
            [
                ffmpeg_bin(), "-hide_banner", "-loglevel", "info", "-i", str(output),
                "-map", "0:v:0", "-c", "copy", "-bsf:v", "trace_headers",
                "-f", "null", "-",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stderr
        idr_count = len(re.findall(r"nal_unit_type\s+01(?:0011|0100) = (?:19|20)", headers))
        assert idr_count == len(keyframes)
