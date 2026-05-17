"""Split a long input into keyframe-aligned segments, process each, then concat.

Strategy: keyframe-aware split + per-segment process + concat demuxer (stream
copy at the seams). Audio main track is processed separately on the full
source for clean loudnorm / pitch behaviour.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_video_segment_command,
)
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

# ---- planning ----------------------------------------------------------------

def list_keyframes(source: Path) -> list[float]:
    """Return keyframe presentation timestamps (seconds) for stream v:0.

    Uses ffprobe `-skip_frame nokey` so only keyframes are read.
    For very long files this still reads metadata for each keyframe — fast
    relative to encoding, but not free.
    """
    cmd = [
        ffprobe_bin(),
        "-v", "error",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_frames",
        "-show_entries", "frame=pts_time",
        "-of", "json",
        str(source),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"ffprobe keyframe scan failed for {source}: {exc.stderr.strip()}"
        ) from exc
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"keyframe scan emitted invalid JSON: {exc}") from exc

    frames = raw.get("frames", [])
    ks: list[float] = []
    for f in frames:
        t = f.get("pts_time")
        if t is None:
            continue
        try:
            ks.append(float(t))
        except (TypeError, ValueError):
            continue
    return sorted(set(ks))


def plan_segments(plan: Plan, target_size_sec: float = 600.0) -> list[Segment]:
    """Greedy: walk keyframes, cut whenever the running span >= target.

    For short inputs (< target) we return a single segment covering [0, duration].
    Boundaries are always exact keyframe timestamps so per-segment stream-copy
    extraction is clean.
    """
    duration = plan.source.duration_sec
    if duration <= 0:
        raise PipelineError("source duration is 0; cannot segment")

    keyframes = list_keyframes(plan.source.path)
    # Always include 0 as the implicit first keyframe.
    if not keyframes or keyframes[0] > 0.001:
        keyframes = [0.0, *keyframes]
    # Append duration as the terminating boundary.
    if keyframes[-1] < duration - 0.001:
        keyframes = [*keyframes, duration]

    segments: list[Segment] = []
    start = keyframes[0]
    last = keyframes[0]
    idx = 0
    for kf in keyframes[1:]:
        span = kf - start
        if span >= target_size_sec and kf < duration:
            segments.append(
                Segment(idx=idx, start_sec=start, end_sec=kf, status="pending")
            )
            idx += 1
            start = kf
        last = kf

    # Final segment closes on duration / last keyframe.
    end = max(last, duration)
    segments.append(Segment(idx=idx, start_sec=start, end_sec=end, status="pending"))
    return segments


# ---- per-segment ops ---------------------------------------------------------

def stream_copy_extract(segment: Segment, source: Path, dest: Path) -> None:
    """Fast stream-copy cut between two keyframes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{segment.start_sec:.6f}",
        "-to", f"{segment.end_sec:.6f}",
        "-i", str(source),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"stream_copy_extract failed for segment {segment.idx}: {exc.stderr.strip()}"
        ) from exc


def process_video_segment(
    segment: Segment,
    plan: Plan,
    work_dir: Path,
    *,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
) -> tuple[Path, Path]:
    """Extract + apply video transforms. Returns (src_seg_path, out_seg_path)."""
    src = work_dir / f"seg_{segment.idx:04d}_src.mkv"
    out = work_dir / f"seg_{segment.idx:04d}.mkv"
    stream_copy_extract(segment, plan.source.path, src)
    cmd = build_video_segment_command(plan, src, out)
    run_ffmpeg(
        cmd, output=out, on_event=on_event, cancel_token=cancel_token,
        log_path=out.with_suffix(".mkv.log"),
    )
    return src, out


def process_main_audio(
    plan: Plan,
    work_dir: Path,
    *,
    loudnorm_measurement: LoudnormMeasurement | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
) -> tuple[Path | None, LoudnormMeasurement | None]:
    """Process the full source's main audio in one pass. Returns (path, measurement)."""
    out = work_dir / "main_audio.m4a"
    cmd, measurement = build_main_audio_command(
        plan, out, loudnorm_measurement=loudnorm_measurement
    )
    if not cmd.args:
        return None, measurement
    run_ffmpeg(
        cmd, output=out, on_event=on_event, cancel_token=cancel_token,
        log_path=out.with_suffix(".m4a.log"),
    )
    return out, measurement


# ---- concat ------------------------------------------------------------------

def concat_segments(
    video_segments: list[Path],
    main_audio: Path | None,
    output: Path,
    metadata_args: list[str],
    *,
    map_chapters_from: Path | None = None,
) -> None:
    """Concatenate stream-copy segments and mux in the separately-processed audio."""
    if not video_segments:
        raise PipelineError("no video segments to concat")

    concat_list = output.parent / "concat.txt"
    concat_list.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{p.absolute()}'" for p in video_segments]
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd: list[str] = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
    ]
    if main_audio is not None:
        cmd += ["-i", str(main_audio)]
    if map_chapters_from is not None:
        cmd += ["-i", str(map_chapters_from)]

    cmd += ["-map", "0:v:0"]
    if main_audio is not None:
        cmd += ["-map", "1:a:0"]
    # Preserve any audio tracks already inside the concatenated stream beyond track 0.
    cmd += ["-map", "0:a:1?", "-map", "0:a:2?"]
    cmd += ["-map", "0:s?"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    if map_chapters_from is not None:
        chap_idx = 2 if main_audio is not None else 1
        cmd += ["-map_chapters", str(chap_idx)]
    else:
        cmd += ["-map_chapters", "-1"]
    cmd += ["-movflags", "+faststart"]
    cmd += metadata_args
    cmd += [str(output)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(f"concat failed: {exc.stderr.strip()[-500:]}") from exc
