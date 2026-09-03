"""Real-FFmpeg VFR regression coverage across segmented processing and concat."""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.models import EncoderCandidate, Plan, Profile, TransformConfig
from yt_uniquifier.core.orchestrator import RunOptions, run_full
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.segmenter import list_keyframes, plan_segments


def _make_vfr_clip(output: Path) -> None:
    """Create 6 seconds containing 30, 20, then 60 fps timestamp regions."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000:duration=6",
            "-vf",
            "select='if(lt(t,2),not(mod(n,2)),if(lt(t,4),not(mod(n,3)),1))'",
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*1)",
            "-c:a", "aac", "-shortest", str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _probe_json(path: Path, *entries: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames",
            "-show_entries", ":".join(entries), "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _video_pts(path: Path) -> list[float]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time",
            "-of", "csv=p=0", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [
        float(line.split(",", 1)[0])
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _has_delta(deltas: list[float], expected: float) -> bool:
    return any(abs(delta - expected) <= 0.002 for delta in deltas)


def _plan(source_path: Path, *, name: str) -> Plan:
    source = probe(source_path)
    profile = Profile(name=name, transforms=[], skip_watermark_check=True)
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    return Plan(
        source=source,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source, profile, encoder),
    )


def _make_cfr_clip(
    output: Path,
    *,
    rate: str,
    duration: float,
    keyint: int | None = None,
    start_offset: float = 0.0,
    dynamic_life: bool = False,
) -> None:
    video_source = (
        f"life=size=160x90:rate={rate}:ratio=0.3:seed=42"
        if dynamic_life
        else f"testsrc2=size=160x90:rate={rate}:duration={duration}"
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", video_source,
        "-f", "lavfi", "-i",
        f"sine=frequency=997:sample_rate=48000:duration={duration}",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    ]
    if keyint is None:
        command += ["-force_key_frames", "expr:gte(t,n_forced*1)"]
    else:
        command += [
            "-x264-params",
            f"keyint={keyint}:min-keyint={keyint}:scenecut=0",
        ]
    command += ["-c:a", "aac"]
    if start_offset:
        command += ["-output_ts_offset", str(start_offset)]
    command += ["-shortest", str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)


def _stream_timeline(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    streams = _probe_json(
        path,
        "stream=index,codec_type,start_time,duration,nb_read_frames,sample_rate",
    )["streams"]
    return (
        next(stream for stream in streams if stream["codec_type"] == "video"),
        next(stream for stream in streams if stream["codec_type"] == "audio"),
    )


def _decoded_gray_frames(path: Path, *, width: int = 64, height: int = 36) -> list[bytes]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", "0:v:0", "-vf", f"scale={width}:{height}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    frame_size = width * height
    assert len(result.stdout) % frame_size == 0
    return [
        result.stdout[offset:offset + frame_size]
        for offset in range(0, len(result.stdout), frame_size)
    ]


def _mean_absolute_difference(left: bytes, right: bytes) -> float:
    assert len(left) == len(right)
    return sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left)


def _make_av_impulse_clip(output: Path) -> None:
    event_expression = "+".join(
        f"lt(abs(t-{timestamp})\\,0.01)" for timestamp in (0.5, 3.0, 6.5)
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=black:size=160x90:rate=24:duration=7,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:"
            f"enable={event_expression}",
            "-f", "lavfi", "-i",
            f"aevalsrc=if({event_expression}\\,0.9\\,0):s=48000:d=7",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-x264-params", "keyint=24:min-keyint=24:scenecut=0",
            "-c:a", "aac", "-shortest", str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _audio_peak_times(
    path: Path,
    expected_times: tuple[float, ...],
    *,
    sample_rate: int = 48_000,
) -> list[float]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    samples = [sample[0] for sample in struct.iter_unpack("=f", result.stdout)]
    peaks: list[float] = []
    for expected in expected_times:
        first = max(0, round((expected - 0.25) * sample_rate))
        last = min(len(samples), round((expected + 0.25) * sample_rate))
        peak = max(range(first, last), key=lambda index: abs(samples[index]))
        peaks.append(peak / sample_rate)
    return peaks


def _video_flash_times(path: Path, expected_times: tuple[float, ...]) -> list[float]:
    frames = _decoded_gray_frames(path, width=16, height=9)
    pts = _video_pts(path)
    assert len(frames) == len(pts)
    result: list[float] = []
    for expected in expected_times:
        candidates = [
            (sum(frame) / len(frame), timestamp)
            for frame, timestamp in zip(frames, pts, strict=True)
            if abs(timestamp - expected) <= 0.25
        ]
        assert candidates
        result.append(max(candidates)[1])
    return result


@needs_ffmpeg
@pytest.mark.integration
def test_segmented_vfr_preserves_frames_cadence_and_av_timeline(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / "source-vfr.mp4"
    output_path = tmp_path / "output-vfr.mp4"
    _make_vfr_clip(source_path)

    source = probe(source_path)
    assert source.video[0].fps == pytest.approx(110 / 3, rel=1e-4)
    profile = Profile(name="vfr-contract", transforms=[], skip_watermark_check=True)
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(
        source=source,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source, profile, encoder),
    )
    summary = run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )
    assert summary.segments_done >= 5

    source_pts = _video_pts(source_path)
    output_pts = _video_pts(output_path)
    assert len(source_pts) == 220
    assert len(output_pts) == len(source_pts)
    pairs = zip(output_pts, output_pts[1:], strict=False)
    deltas = [right - left for left, right in pairs]
    assert all(delta > 0 for delta in deltas)
    assert _has_delta(deltas, 1 / 60)
    assert _has_delta(deltas, 1 / 30)
    assert _has_delta(deltas, 1 / 20)

    streams = _probe_json(
        output_path,
        "stream=index,codec_type,start_time,duration,nb_read_frames",
    )["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    video_end = float(video["start_time"]) + float(video["duration"])
    audio_end = float(audio["start_time"]) + float(audio["duration"])
    assert abs(video_end - 6.0) <= 0.02
    assert abs(audio_end - video_end) <= 0.05


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize(
    "rate",
    ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"],
)
def test_segmented_cfr_matrix_preserves_frames_pts_and_av_timeline(
    rate: str, tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / f"source-{rate.replace('/', '_')}.mp4"
    output_path = tmp_path / f"output-{rate.replace('/', '_')}.mp4"
    _make_cfr_clip(source_path, rate=rate, duration=3.0)
    plan = _plan(source_path, name=f"cfr-{rate}")

    summary = run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / f"work-{rate.replace('/', '_')}",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )
    assert summary.segments_done >= 3

    source_pts = _video_pts(source_path)
    output_pts = _video_pts(output_path)
    assert len(output_pts) == len(source_pts)
    assert all(right > left for left, right in zip(output_pts, output_pts[1:], strict=False))
    source_zero = source_pts[0]
    output_zero = output_pts[0]
    assert max(
        abs((actual - output_zero) - (expected - source_zero))
        for expected, actual in zip(source_pts, output_pts, strict=True)
    ) <= 0.002

    video, audio = _stream_timeline(output_path)
    video_end = float(video["start_time"]) + float(video["duration"])
    audio_end = float(audio["start_time"]) + float(audio["duration"])
    sample_rate = int(audio["sample_rate"])
    frame_period = 1.0 / plan.source.video[0].fps
    # AAC packets contain 1024 samples; the final packet may legally extend
    # one audio frame beyond the last video frame without audible desync.
    assert abs(audio_end - video_end) <= max(0.020, frame_period, 1024 / sample_rate)


@needs_ffmpeg
@pytest.mark.integration
def test_nonzero_container_start_is_normalized_before_segmentation(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / "source-offset.mp4"
    output_path = tmp_path / "output-offset.mp4"
    _make_cfr_clip(
        source_path,
        rate="24",
        duration=5.0,
        start_offset=5.0,
    )
    plan = _plan(source_path, name="nonzero-start")

    keyframes = list_keyframes(source_path, force=True)
    assert keyframes == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0], abs=0.002)
    segments = plan_segments(plan, target_size_sec=1.0)
    assert len(segments) == 5
    assert segments[0].start_sec == 0.0
    assert segments[-1].end_sec == pytest.approx(plan.source.duration_sec)
    assert sum(segment.end_sec - segment.start_sec for segment in segments) == pytest.approx(
        plan.source.duration_sec,
    )

    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-offset",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )
    assert len(_video_pts(output_path)) == len(_video_pts(source_path))


@needs_ffmpeg
@pytest.mark.integration
def test_sparse_long_gop_preserves_content_at_every_concat_seam(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / "source-long-gop.mp4"
    output_path = tmp_path / "output-long-gop.mp4"
    _make_cfr_clip(
        source_path,
        rate="24",
        duration=7.0,
        keyint=72,
        dynamic_life=True,
    )
    plan = _plan(source_path, name="long-gop-seams")
    segments = plan_segments(plan, target_size_sec=1.0)
    assert [(segment.start_sec, segment.end_sec) for segment in segments] == pytest.approx(
        [(0.0, 3.0), (3.0, 6.0), (6.0, plan.source.duration_sec)],
        abs=0.002,
    )

    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-long-gop",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )
    source_frames = _decoded_gray_frames(source_path)
    output_frames = _decoded_gray_frames(output_path)
    assert len(output_frames) == len(source_frames)

    for seam_sec in (3.0, 6.0):
        seam_frame = round(seam_sec * 24)
        for frame_index in range(seam_frame - 2, seam_frame + 3):
            direct = _mean_absolute_difference(
                source_frames[frame_index], output_frames[frame_index],
            )
            shifted = min(
                _mean_absolute_difference(
                    source_frames[frame_index - 1], output_frames[frame_index],
                ),
                _mean_absolute_difference(
                    source_frames[frame_index + 1], output_frames[frame_index],
                ),
            )
            assert direct < shifted * 0.5, (
                f"frame {frame_index} around seam {seam_sec}s is closer to an adjacent "
                f"source frame ({shifted:.3f}) than its matching frame ({direct:.3f})"
            )


@needs_ffmpeg
@pytest.mark.integration
def test_internal_audio_impulses_remain_synced_with_video_flashes_across_seams(
    tmp_path: Path, isolated_cache: Path,
) -> None:
    source_path = tmp_path / "source-av-impulses.mp4"
    output_path = tmp_path / "output-av-impulses.mp4"
    _make_av_impulse_clip(source_path)
    source = probe(source_path)
    profile = Profile(
        name="av-impulse-sync",
        transforms=[TransformConfig(
            id="audio.pitch_tempo",
            params={"pitch": 1.0004, "tempo": 1.0},
        )],
        skip_watermark_check=True,
    )
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(
        source=source,
        profile=profile,
        encoder=encoder,
        plan_hash=compute_plan_hash(source, profile, encoder),
    )
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work-av-impulses",
            output=output_path,
            target_segment_sec=1.0,
        ),
    )

    expected_events = (0.5, 3.0, 6.5)
    source_video_events = _video_flash_times(source_path, expected_events)
    output_video_events = _video_flash_times(output_path, expected_events)
    source_audio_events = _audio_peak_times(source_path, expected_events)
    output_audio_events = _audio_peak_times(output_path, expected_events)
    tolerance = max(1 / 24, 1024 / 48_000)
    for index, expected in enumerate(expected_events):
        assert output_video_events[index] == pytest.approx(
            source_video_events[index], abs=1 / 24,
        )
        assert output_audio_events[index] == pytest.approx(
            source_audio_events[index], abs=tolerance,
        )
        assert abs(output_audio_events[index] - output_video_events[index]) <= tolerance, (
            f"A/V event near {expected}s drifted: video={output_video_events[index]:.6f}, "
            f"audio={output_audio_events[index]:.6f}"
        )
