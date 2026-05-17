"""Split a long input into keyframe-aligned segments, process each, then concat.

Strategy: keyframe-aware split + per-segment process + concat demuxer (stream
copy at the seams). Audio main track is processed separately on the full
source for clean loudnorm / pitch behaviour.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_video_segment_command,
)
from yt_uniquifier.core.qa.hashes import md5_file
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

KEYFRAME_CACHE_DIR = Path.home() / ".cache" / "yt_uniquifier" / "keyframes"
KEYFRAME_CACHE_TTL_SEC = 30 * 24 * 3600  # 30 days

# ---- planning ----------------------------------------------------------------

def list_keyframes(source: Path, *, force: bool = False) -> list[float]:
    """Return keyframe presentation timestamps (seconds) for stream v:0.

    Cached at ~/.cache/yt_uniquifier/keyframes/<md5>.json for 30 days. For
    1080p+ feature-length files this saves 30-60s per resume.
    Pass force=True to bypass.
    """
    cached = _load_keyframe_cache(source) if not force else None
    if cached is not None:
        return cached

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
    result = sorted(set(ks))
    _save_keyframe_cache(source, result)
    return result


def _keyframe_cache_path(source: Path) -> Path:
    digest = md5_file(source)
    return KEYFRAME_CACHE_DIR / f"{digest}.json"


def _load_keyframe_cache(source: Path) -> list[float] | None:
    path = _keyframe_cache_path(source)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if time.time() - raw.get("written_at", 0) > KEYFRAME_CACHE_TTL_SEC:
        return None
    kfs = raw.get("keyframes")
    if not isinstance(kfs, list):
        return None
    return [float(x) for x in kfs]


def _save_keyframe_cache(source: Path, kfs: list[float]) -> None:
    path = _keyframe_cache_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "written_at": time.time(), "keyframes": kfs}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


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


def parallel_safe(plan: Plan) -> int:
    """Return max concurrent encode workers safe for this plan's encoder.

    v0.3+: each EncoderCandidate carries `max_parallel` (detected at probe
    time — NVENC consumer=3, pro=8, CPU=cpu_count()//2, etc.). The
    orchestrator caps user-requested workers at this number.

    Returns >= 1 always.
    """
    return max(1, plan.encoder.max_parallel)


def process_video_segments_parallel(
    pending: list[Segment],
    plan: Plan,
    work_dir: Path,
    *,
    workers: int = 1,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    on_segment_done: Callable[[int, Path, Path], None] | None = None,
) -> list[tuple[int, Path, Path]]:
    """Run `process_video_segment` over `pending` segments concurrently.

    Falls back to sequential when workers <= 1 or the encoder isn't CPU-only.
    Each worker is encouraged to use a single ffmpeg thread (`OMP_NUM_THREADS=1`)
    so concurrency doesn't oversubscribe the CPU.

    Returns list of (idx, src_path, out_path) tuples in completion order.
    Cancel: best-effort — already-running workers finish their current ffmpeg.
    """
    cap = parallel_safe(plan)
    effective = min(max(1, workers), cap)
    if effective < workers and on_event is not None:
        on_event(RunEvent(kind="log", payload={
            "phase": "workers",
            "message": (
                f"workers downgraded {workers} → {effective} "
                f"({plan.encoder.name} cap)"
            ),
        }))

    if effective <= 1:
        results: list[tuple[int, Path, Path]] = []
        for seg in pending:
            if cancel_token and cancel_token.is_cancelled():
                raise PipelineError("cancelled by user")
            src, out = process_video_segment(
                seg, plan, work_dir,
                on_event=on_event, cancel_token=cancel_token,
            )
            results.append((seg.idx, src, out))
            if on_segment_done:
                on_segment_done(seg.idx, src, out)
        return results

    # Parallel path: ThreadPoolExecutor + per-call OMP_NUM_THREADS=1.
    # We use threads (not processes) because each segment spawns its own ffmpeg
    # subprocess via run_ffmpeg, which already releases the GIL.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective) as pool:
        futures = {
            pool.submit(
                process_video_segment, seg, plan, work_dir,
                on_event=on_event, cancel_token=cancel_token,
            ): seg
            for seg in pending
        }
        for fut in concurrent.futures.as_completed(futures):
            seg = futures[fut]
            src, out = fut.result()
            results.append((seg.idx, src, out))
            if on_segment_done:
                on_segment_done(seg.idx, src, out)
    return results


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
