"""Split a long input into keyframe-aligned segments, process each, then concat.

Strategy: keyframe-aware split + per-segment process + concat demuxer (stream
copy at the seams). Audio main track is processed separately on the full
source for clean loudnorm / pitch behaviour.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_main_audio_command_windowed,
    build_video_segment_command,
)
from yt_uniquifier.core.qa.hashes import md5_file
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.seed_resolver import derive_segment_seed
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

_log = logging.getLogger(__name__)

KEYFRAME_CACHE_TTL_SEC = 30 * 24 * 3600  # 30 days


def _keyframe_cache_dir() -> Path:
    """Return the cache dir, honoring runtime reassignment of
    ``KEYFRAME_CACHE_DIR`` (tests monkeypatch this constant).
    """
    import sys as _sys
    from typing import cast
    return cast(Path, _sys.modules[__name__].KEYFRAME_CACHE_DIR)


# Default resolved at import. Tests reassign / monkeypatch this.
KEYFRAME_CACHE_DIR = Path.home() / ".cache" / "yt_uniquifier" / "keyframes"

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
    # Deduplicate with 1 ms tolerance: containers with imprecise PTS can
    # emit `10.000000` and `10.000001` for the same keyframe, which a
    # plain `set()` keeps as distinct, producing a 1 µs segment that
    # passes plan_segments but fails stream_copy_extract with empty
    # output.
    result = _dedup_keyframes(sorted(ks))
    _save_keyframe_cache(source, result)
    return result


def _dedup_keyframes(sorted_ks: list[float], tol_sec: float = 1e-3) -> list[float]:
    """Collapse keyframes within `tol_sec` of the previously-kept one."""
    out: list[float] = []
    for t in sorted_ks:
        if not out or t - out[-1] > tol_sec:
            out.append(t)
    return out


def _keyframe_cache_path(source: Path) -> Path:
    digest = md5_file(source)
    return _keyframe_cache_dir() / f"{digest}.json"


def _load_keyframe_cache(source: Path) -> list[float] | None:
    path = _keyframe_cache_path(source)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Surface the corruption so a silently-bypassed cache doesn't
        # masquerade as a 30-60s "slow probe" on every subsequent run.
        _log.warning("keyframe cache unreadable at %s: %s; will re-scan", path, exc)
        return None
    if not isinstance(raw, dict):
        _log.warning("keyframe cache schema invalid at %s; will re-scan", path)
        return None
    if time.time() - raw.get("written_at", 0) > KEYFRAME_CACHE_TTL_SEC:
        return None
    kfs = raw.get("keyframes")
    if not isinstance(kfs, list):
        _log.warning("keyframe cache missing keyframes list at %s; will re-scan", path)
        return None
    return [float(x) for x in kfs]


def _save_keyframe_cache(source: Path, kfs: list[float]) -> None:
    path = _keyframe_cache_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "written_at": time.time(), "keyframes": kfs}
    # PID suffix prevents tmp-name collisions across concurrent batch jobs
    # processing the same source. fsync before os.replace matches the
    # checkpoint.py atomic-write pattern — without it a crash between
    # rename and page-write can leave a zero-byte cache file that forces
    # a 30-60s full re-scan on the next run.
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload))
        fh.flush()
        os.fsync(fh.fileno())
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
    """Fast stream-copy cut between two keyframes.

    Uses ``-t (end-start)`` rather than ``-to end`` because some MP4 sources
    have packet PTS that extend past the container-reported duration (edit
    lists, partial trailing samples). With ``-to`` the .mkv extract preserves
    every packet up to the source EOF, making single-segment outputs longer
    than the input. ``-t`` clamps to an exact wall-clock length.
    (CRIT-2 from the 2026-05-30 test report.)
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    span = max(0.001, segment.end_sec - segment.start_sec)
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{segment.start_sec:.6f}",
        "-i", str(source),
        "-t", f"{span:.6f}",
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


def _plan_for_segment(plan: Plan, segment_idx: int) -> Plan:
    """For seed_strategy='divergent', return a Plan copy with a per-segment seed.

    For all other strategies returns `plan` unchanged. The derived seed is
    deterministic from (plan_hash, idx, run_seed) so resume reproduces it.
    """
    if plan.profile.seed_strategy != "divergent":
        return plan
    seg_seed = derive_segment_seed(plan.plan_hash, segment_idx, plan.run_seed)
    return plan.model_copy(update={"run_seed": seg_seed})


def process_video_segment(
    segment: Segment,
    plan: Plan,
    work_dir: Path,
    *,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Extract + apply video transforms. Returns (src_seg_path, out_seg_path)."""
    src = work_dir / f"seg_{segment.idx:04d}_src.mkv"
    out = work_dir / f"seg_{segment.idx:04d}.mkv"
    stream_copy_extract(segment, plan.source.path, src)
    seg_plan = _plan_for_segment(plan, segment.idx)
    cmd = build_video_segment_command(seg_plan, src, out)
    run_ffmpeg(
        cmd, output=out, on_event=on_event, cancel_token=cancel_token,
        log_path=out.with_suffix(".mkv.log"),
        extra_env=extra_env,
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

    # Parallel path: ThreadPoolExecutor.
    # We use threads (not processes) because each segment spawns its own
    # ffmpeg subprocess via run_ffmpeg, which already releases the GIL.
    # OMP_NUM_THREADS=1 is wanted to prevent libavfilter from
    # oversubscribing the CPU. The previous implementation mutated
    # os.environ globally, which raced between concurrent batch
    # invocations and leaked the value to every unrelated subprocess
    # the host launched. Instead, pass it through `extra_env` so it
    # lives only on the ffmpeg child's environment — never on the
    # parent process.
    extra_env: dict[str, str] = {}
    if os.environ.get("OMP_NUM_THREADS") is None:
        extra_env["OMP_NUM_THREADS"] = "1"
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective) as pool:
        futures = {
            pool.submit(
                process_video_segment, seg, plan, work_dir,
                on_event=on_event, cancel_token=cancel_token,
                extra_env=extra_env or None,
            ): seg
            for seg in pending
        }
        try:
            for fut in concurrent.futures.as_completed(futures):
                seg = futures[fut]
                src, out = fut.result()
                results.append((seg.idx, src, out))
                if on_segment_done:
                    on_segment_done(seg.idx, src, out)
        except BaseException:
            # First failure (or external cancel) — signal all in-flight
            # workers to stop ASAP instead of letting `ThreadPoolExecutor.
            # __exit__` block on full segment encodes. `runner.run` checks
            # `cancel_token` between every progress line, so each worker
            # exits within seconds rather than at the next segment
            # boundary (which could be 10+ minutes).
            if cancel_token is not None:
                cancel_token.cancel()
            for f in futures:
                f.cancel()
            raise
    return results


def process_main_audio(
    plan: Plan,
    work_dir: Path,
    *,
    loudnorm_measurement: LoudnormMeasurement | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
) -> tuple[Path | None, LoudnormMeasurement | None]:
    """Process the full source's main audio. Returns (path, measurement).

    For `seed_strategy='divergent'` (v0.4.2+), audio is windowed into
    ~60 s pieces each with their own per-window seed. Otherwise audio
    runs in a single pass on the full source (legacy behavior).
    """
    out = work_dir / "main_audio.m4a"
    if plan.profile.seed_strategy == "divergent":
        cmd, measurement = build_main_audio_command_windowed(
            plan, out, loudnorm_measurement=loudnorm_measurement
        )
    else:
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
    work_dir: Path,
    map_chapters_from: Path | None = None,
    audio_passthrough_count: int = 2,
    target_duration_sec: float | None = None,
) -> None:
    """Concatenate stream-copy segments and mux in the separately-processed audio.

    The transient concat-demuxer list is written to ``work_dir`` (per-job
    unique) — NOT ``output.parent``. Multiple concurrent ``yt-uniq batch``
    jobs writing into a shared output directory would otherwise race on
    the same ``output.parent / 'concat.txt'`` path and silently swap each
    other's content.
    """
    if not video_segments:
        raise PipelineError("no video segments to concat")

    work_dir.mkdir(parents=True, exist_ok=True)
    concat_list = work_dir / "concat.txt"
    # ffmpeg concat demuxer wraps paths in single quotes; literal `'` inside
    # a path must be escaped as `'\''` (close, escaped-quote, reopen).
    # Without this, a work_dir like `~/Downloads/it's a test/` fails with a
    # cryptic parse error.
    _Q = "'"
    _ESC_Q = "'\\''"
    lines = [f"file '{str(p.absolute()).replace(_Q, _ESC_Q)}'" for p in video_segments]
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
    # Preserve any audio tracks already inside the concatenated stream
    # beyond track 0. `audio_passthrough_count` lets callers extend the
    # range for sources with > 2 extra audio dorozhki without dropping
    # tracks silently. The default (2) preserves legacy behaviour for
    # the 99% common case.
    for n in range(1, audio_passthrough_count + 1):
        cmd += ["-map", f"0:a:{n}?"]
    cmd += ["-map", "0:s?"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    if map_chapters_from is not None:
        chap_idx = 2 if main_audio is not None else 1
        cmd += ["-map_chapters", str(chap_idx)]
    else:
        cmd += ["-map_chapters", "-1"]
    cmd += ["-movflags", "+faststart"]
    # Trim final output to source duration. Some MP4 sources have packet PTS
    # extending past container.duration (edit lists, partial trailing
    # samples); without `-t` the concatenated mkv preserves those packets
    # and the muxed mp4 ends up longer than the input. (CRIT-2 from the
    # 2026-05-30 test report — the fix at `stream_copy_extract` wasn't
    # enough because PTS propagate through stream-copy.)
    if target_duration_sec is not None and target_duration_sec > 0:
        cmd += ["-t", f"{target_duration_sec:.6f}"]
    cmd += metadata_args
    cmd += [str(output)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        # Show both head + tail of stderr. ffmpeg often emits the real
        # cause in the first few lines (bad path, codec mismatch) and a
        # tail-only window of 500 chars would silently hide it behind
        # cosmetic warnings.
        full = exc.stderr.strip() if exc.stderr else ""
        head = full[:300]
        tail = full[-500:] if len(full) > 800 else ""
        snippet = head + ("\n…\n" + tail if tail else "")
        raise PipelineError(f"concat failed: {snippet}") from exc
