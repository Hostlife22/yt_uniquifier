"""Split a long input into keyframe-aligned segments, process each, then concat.

Strategy: keyframe-aware split + per-segment process + concat demuxer (stream
copy at the seams). Audio main track is processed separately on the full
source for clean loudnorm / pitch behaviour.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from bisect import bisect_left
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.audio_windows import verify_audio_filters_available
from yt_uniquifier.core.auxiliary_streams import (
    AuxiliaryStream,
    unsupported_auxiliary_streams,
)
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import AudioStream, Plan, Segment
from yt_uniquifier.core.output_reservation import (
    RunAdmission,
    RunAdmissionError,
    RunAdmissionFull,
)
from yt_uniquifier.core.pipeline import (
    BuiltCommand,
    build_main_audio_command,
    build_main_audio_command_windowed,
    build_video_segment_command,
    build_video_segment_command_fused,
)
from yt_uniquifier.core.resource_budget import resource_pool_dir
from yt_uniquifier.core.runner import CancelToken, PauseToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.seed_resolver import derive_segment_seed
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

_log = logging.getLogger(__name__)

KEYFRAME_CACHE_TTL_SEC = 30 * 24 * 3600  # 30 days
KEYFRAME_CACHE_SCHEMA_VERSION = 2
_CACHE_REPLACE_ATTEMPTS = 12
_CACHE_REPLACE_MAX_DELAY_SEC = 0.1

# Shared within one Python process, so overlapping web/GUI/batch runs consume
# one encoder/device budget instead of each independently using max_parallel.
_RESOURCE_BUDGETS: dict[str, tuple[int, threading.BoundedSemaphore]] = {}
_RESOURCE_BUDGETS_LOCK = threading.Lock()


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
    """Return keyframe timestamps relative to the start of stream v:0.

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
        "-show_streams",
        "-show_frames",
        "-show_entries", "frame=pts_time:stream=start_time",
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
    # ffprobe reports frame PTS in the container timeline.  ``-ss`` input
    # seeking, segment durations and ``SourceMeta.duration_sec`` are relative
    # to the media start, so carrying an MP4/MOV edit-list offset (or an MPEG-TS
    # start near 1.4 s) into the planner creates boundaries beyond duration.
    # The old cache schema persisted those absolute values; schema v2 below
    # deliberately invalidates it.
    stream_start = 0.0
    streams = raw.get("streams", [])
    if isinstance(streams, list) and streams:
        try:
            stream_start = float(streams[0].get("start_time", 0.0))
        except (AttributeError, TypeError, ValueError):
            stream_start = 0.0
    ks: list[float] = []
    for f in frames:
        t = f.get("pts_time")
        if t is None:
            continue
        try:
            # Clamp edit-list preroll to zero. It is useful to the decoder but
            # is not part of the user-visible output timeline.
            ks.append(max(0.0, float(t) - stream_start))
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
    """Return the on-disk cache key for ``source``'s keyframe list.

    B1 (v0.6.0): the key is ``(st_size, st_mtime_ns, head+tail md5)``
    rather than a full-file MD5. On a 180 GB 4K HDR master at 500 MB/s
    SSD read speed, the full MD5 took 60-360 s before the first ffmpeg
    fork. ``stat`` + 8 KB of disk I/O is microseconds.

    R9 (v0.7): the original ``(st_size, st_mtime_ns)`` key collided
    on Windows for two distinct small files written in the same NTFS
    timer tick — surfaced by the
    ``test_different_file_different_cache`` unit test once R8
    unblocked the matrix. Added a 12-char prefix of MD5 over the
    first 4 KB and the last 4 KB so any content difference outside the
    interior of a giant file is captured cheaply.

    Trade-off: a file rewritten in place with bit-identical contents
    but a fresh mtime triggers a cache miss (acceptable false
    invalidation). A file replaced with identical-size content, a
    forced-set mtime, AND matching head + tail is a deliberately
    pathological case we accept hitting the cache for.
    Path-independent by construction, so the cache survives moving a
    source across mounts.
    """
    st = source.stat()
    # Non-security cache discriminator; cache entries are revalidated before use.
    h = hashlib.md5(usedforsecurity=False)
    try:
        with source.open("rb") as fh:
            h.update(fh.read(4096))
            if st.st_size > 8192:
                fh.seek(-4096, os.SEEK_END)
                h.update(fh.read(4096))
    except OSError:
        # Surface as "no fingerprint"; the (size, mtime_ns) prefix is
        # still distinct enough for the common case, and a re-probe is
        # always safe.
        pass
    fp = h.hexdigest()[:12]
    return _keyframe_cache_dir() / f"{st.st_size}_{st.st_mtime_ns}_{fp}.json"


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
    if raw.get("schema_version") != KEYFRAME_CACHE_SCHEMA_VERSION:
        _log.info("keyframe cache schema outdated at %s; will re-scan", path)
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
    payload = {
        "schema_version": KEYFRAME_CACHE_SCHEMA_VERSION,
        "written_at": time.time(),
        "keyframes": kfs,
    }
    # PID + random-hex tmp suffix + fsync before os.replace. Mirrors the
    # encoder.py::_save_cache pattern so the keyframe cache survives the
    # same two failure modes:
    #   1) Concurrent `yt-uniq batch` jobs racing on the same source —
    #      PID alone prevents cross-process tmp collisions.
    #   2) Same-process re-entry (e.g. two list_keyframes calls in a
    #      short window from one worker) — a PID-only tmp can still
    #      collide and produce a torn read on the loser. The random
    #      suffix makes the tmp name unique per call.
    # Without fsync a crash between the page-cache write and the rename
    # leaves a zero-byte cache file that forces a 30-60s full re-scan.
    tmp = path.with_name(
        f"{path.stem}.{os.getpid()}.{secrets.token_hex(4)}.json.tmp"
    )
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload))
        fh.flush()
        os.fsync(fh.fileno())
    try:
        _replace_cache_file(tmp, path)
    finally:
        # Usually ``tmp`` no longer exists after a successful replace.  If all
        # retries fail, do not leave stale per-call files in the shared cache.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            _log.debug("could not remove keyframe cache temp file %s", tmp)


def _replace_cache_file(tmp: Path, destination: Path) -> None:
    """Atomically replace a cache entry, tolerating transient Windows locks.

    Windows can briefly return ``PermissionError`` when two writers replace
    the same destination or an antivirus scanner opens it without delete
    sharing.  The operation is atomic once it succeeds, so a short bounded
    retry preserves the no-torn-write guarantee without serialising unrelated
    cache entries.
    """
    delay = 0.01
    for attempt in range(_CACHE_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, destination)
            return
        except PermissionError:
            if attempt == _CACHE_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, _CACHE_REPLACE_MAX_DELAY_SEC)


def plan_segments(plan: Plan, target_size_sec: float = 600.0) -> list[Segment]:
    """Decide segment boundaries for a Plan, using the profile's mode.

    Modes (see ``Profile.segmentation.mode``):
      * ``keyframe`` — greedy walk over the source's keyframe list,
        cutting whenever the running span reaches ``target_size_sec``.
        This is the v0.7 and earlier behaviour and remains the default.
      * ``scene`` — opt-in PySceneDetect (``[scene]`` extra). Each scene
        boundary is snapped DOWN to the nearest keyframe so the
        downstream ``stream_copy_extract`` invariant (cuts on keyframes)
        is preserved. Multiple scene cuts inside one keyframe interval
        collapse to a single boundary.

    For short inputs (< target_size_sec, or scene mode with no detected
    cuts) we return a single segment covering [0, duration].
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

    mode = plan.profile.segmentation.mode
    if mode == "scene":
        return _plan_scene_segments(
            plan, keyframes, duration, target_size_sec=target_size_sec,
        )
    return _plan_keyframe_segments(keyframes, duration, target_size_sec)


def _plan_keyframe_segments(
    keyframes: list[float], duration: float, target_size_sec: float
) -> list[Segment]:
    """Original v0.7 logic: greedy keyframe accumulation up to target_size_sec."""
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

    end = max(last, duration)
    segments.append(Segment(idx=idx, start_sec=start, end_sec=end, status="pending"))
    return segments


def _plan_scene_segments(
    plan: Plan,
    keyframes: list[float],
    duration: float,
    *,
    target_size_sec: float,
) -> list[Segment]:
    """Plan scene-preferred segments while bounding long and tiny spans.

    Scene cuts remain preferred boundaries, but sparse/static content is also
    split near ``target_size_sec`` using real keyframes. Cuts that would create
    a leading or trailing segment shorter than ``scene_min_length_sec`` are
    discarded. This retains resume granularity for feature-length static scenes
    without sacrificing keyframe-safe scene boundaries.
    """
    from yt_uniquifier.core.scene_detect import (
        detect_scene_boundaries,
        snap_to_keyframes,
    )

    seg_cfg = plan.profile.segmentation
    raw = detect_scene_boundaries(
        plan.source.path,
        threshold=seg_cfg.scene_threshold,
        min_length_sec=seg_cfg.scene_min_length_sec,
    )
    snapped = snap_to_keyframes(
        raw,
        keyframes,
        min_length_sec=seg_cfg.scene_min_length_sec,
    )
    preferred = _filter_scene_edges(
        snapped, duration=duration, min_length_sec=seg_cfg.scene_min_length_sec,
    )
    boundaries = _bound_scene_gaps(
        preferred,
        keyframes=keyframes,
        duration=duration,
        target_size_sec=target_size_sec,
        min_length_sec=seg_cfg.scene_min_length_sec,
    )
    points = [0.0, *boundaries, duration]
    return [
        Segment(
            idx=idx,
            start_sec=start,
            end_sec=end,
            status="pending",
        )
        for idx, (start, end) in enumerate(zip(points, points[1:], strict=False))
    ]


def _filter_scene_edges(
    boundaries: list[float], *, duration: float, min_length_sec: float,
) -> list[float]:
    """Remove scene cuts that would create sub-minimum edge segments."""
    kept: list[float] = []
    previous = 0.0
    for boundary in boundaries:
        if boundary <= previous or boundary >= duration:
            continue
        if boundary - previous < min_length_sec:
            continue
        kept.append(boundary)
        previous = boundary
    while kept and duration - kept[-1] < min_length_sec:
        kept.pop()
    return kept


def _bound_scene_gaps(
    preferred: list[float],
    *,
    keyframes: list[float],
    duration: float,
    target_size_sec: float,
    min_length_sec: float,
) -> list[float]:
    """Add keyframe cuts so no scene gap grows far beyond the target."""
    sorted_keyframes = sorted(set(keyframes))
    result: list[float] = []
    start = 0.0
    for end in [*preferred, duration]:
        while end - start > target_size_sec:
            index = bisect_left(sorted_keyframes, start + target_size_sec)
            if index >= len(sorted_keyframes):
                break
            candidate = sorted_keyframes[index]
            if candidate <= start or candidate >= end:
                break
            # Do not manufacture a tiny segment immediately before a real
            # scene cut. In that case the scene boundary is the better cut.
            if end - candidate < min_length_sec:
                break
            result.append(candidate)
            start = candidate
        if end < duration:
            result.append(end)
            start = end
    return result


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


# B3 (v0.6.0): environment-variable opt-out for the fused single-fork
# segment encode path. Set ``YT_UNIQ_DISABLE_FUSE=1`` to fall back to the
# legacy two-fork extract+re-encode pattern (useful for bisecting an
# obscure PTS regression or for filesystems where ``-ss`` input seek
# behaves unexpectedly).
_DISABLE_FUSE_ENV = "YT_UNIQ_DISABLE_FUSE"


def _fuse_enabled() -> bool:
    val = os.environ.get(_DISABLE_FUSE_ENV, "").strip().lower()
    return val not in ("1", "true", "yes", "on")


def process_video_segment(
    segment: Segment,
    plan: Plan,
    work_dir: Path,
    *,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    pause_token: PauseToken | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path | None, Path]:
    """Apply video transforms to one segment.

    B3 (v0.6.0): default to the FUSED single-fork path —
    ``-ss <start> -i source -t <span>`` plus filter_complex in one
    ffmpeg invocation. The legacy two-fork path
    (stream_copy_extract → build_video_segment_command on the extract)
    is still available via ``YT_UNIQ_DISABLE_FUSE=1`` for emergency
    rollback.

    R5 (v0.8.0): when ``plan.profile.target_vmaf`` is set, the encoded
    segment is scored with libvmaf; if the mean score is below the
    target, the segment is re-encoded with a reduced CRF (or the
    equivalent quality bump on hardware encoders) up to
    ``target_vmaf_max_retries`` times. Emits ``target_vmaf`` events
    after each scored attempt and a terminal ``target_vmaf_failed``
    event if every attempt undershoots the target (the best attempt
    is still kept — never the worse option of an empty segment).

    Returns ``(src_seg_path | None, out_seg_path)``. The fused path
    has no ``_src.mkv`` intermediate so ``src`` is ``None``;
    downstream callers (``CheckpointStore.mark``, cleanup) all handle
    ``None`` already.
    """
    out = work_dir / f"seg_{segment.idx:04d}.mkv"
    seg_plan = _plan_for_segment(plan, segment.idx)

    target = plan.profile.target_vmaf
    step = plan.profile.target_vmaf_step
    max_retries = plan.profile.target_vmaf_max_retries
    if target is not None:
        # Defense in depth for callers such as calibration that deliberately
        # bypass the orchestration-level preflight. The current scorer uses an
        # unmodified source slice, so spatial/timeline transforms make its
        # result unsuitable as encoder-quality feedback.
        from yt_uniquifier.core.preflight import target_vmaf_reference_offenders

        offenders = target_vmaf_reference_offenders(plan)
        if offenders:
            names = ", ".join(offenders)
            raise PipelineError(
                "quality.target_vmaf.unregistered_reference: "
                f"cannot score unregistered transform(s): {names}"
            )
    crf_override: int | None = None
    src: Path | None = None
    attempt = 0

    def _run(cmd: BuiltCommand) -> None:
        # Inject segment idx into every event so downstream consumers
        # (GUI progress bar, history log) can correlate ffmpeg
        # `progress=...` blocks with the segment they belong to.
        if on_event is not None:
            _on_event = on_event

            def _wrap(ev: RunEvent) -> None:
                # A1 (v0.5.5): do NOT mutate the input event's payload.
                if "segment" not in ev.payload:
                    ev = RunEvent(
                        kind=ev.kind,
                        payload={**ev.payload, "segment": segment.idx},
                    )
                _on_event(ev)
            forwarded_on_event: Callable[[RunEvent], None] | None = _wrap
        else:
            forwarded_on_event = None
        run_ffmpeg(
            cmd, output=out, on_event=forwarded_on_event, cancel_token=cancel_token,
            pause_token=pause_token,
            log_path=out.with_suffix(".mkv.log"),
            extra_env=extra_env,
        )

    # Drive the encode (and any retries) by re-using one helper.
    def _encode_once() -> None:
        if _fuse_enabled():
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd_fused = build_video_segment_command_fused(
                seg_plan, segment, plan.source.path, out,
                crf_override=crf_override,
            )
            _run(cmd_fused)
        else:
            nonlocal src
            src = work_dir / f"seg_{segment.idx:04d}_src.mkv"
            if not src.exists():
                stream_copy_extract(segment, plan.source.path, src)
            cmd_legacy = build_video_segment_command(
                seg_plan, src, out, crf_override=crf_override,
            )
            _run(cmd_legacy)

    _encode_once()

    if target is not None:
        score = _score_segment_vmaf(segment, plan.source.path, out)
        best_score = score
        best_crf = crf_override if crf_override is not None else _DEFAULT_CRF_HINT
        best_attempt = attempt
        if on_event is not None:
            on_event(RunEvent(
                kind="target_vmaf",
                payload={
                    "segment": segment.idx,
                    "vmaf": score,
                    "crf": crf_override if crf_override is not None else _DEFAULT_CRF_HINT,
                    "attempt": attempt,
                    "target": target,
                },
            ))
        best_candidate = out.with_name(f"{out.stem}.target_vmaf_best{out.suffix}")
        keep_best = score is not None and score < target and max_retries > 0
        if keep_best:
            shutil.copy2(out, best_candidate)
        try:
            while (
                score is not None
                and score < target
                and attempt < max_retries
            ):
                if cancel_token is not None and cancel_token.is_cancelled():
                    break
                attempt += 1
                current_crf = (
                    crf_override if crf_override is not None else _DEFAULT_CRF_HINT
                )
                crf_override = max(0, current_crf - step)
                _encode_once()
                score = _score_segment_vmaf(segment, plan.source.path, out)
                if score is not None and (best_score is None or score > best_score):
                    best_score = score
                    best_crf = crf_override
                    best_attempt = attempt
                    if keep_best:
                        shutil.copy2(out, best_candidate)
                if on_event is not None:
                    on_event(RunEvent(
                        kind="target_vmaf",
                        payload={
                            "segment": segment.idx,
                            "vmaf": score,
                            "crf": crf_override,
                            "attempt": attempt,
                            "target": target,
                        },
                    ))
        finally:
            if keep_best and best_candidate.exists():
                best_candidate.replace(out)
        if (
            best_score is not None
            and best_score < target
            and on_event is not None
        ):
            on_event(RunEvent(
                kind="target_vmaf_failed",
                payload={
                    "segment": segment.idx,
                    "vmaf": best_score,
                    "crf": best_crf,
                    "best_attempt": best_attempt,
                    "attempts": attempt + 1,
                    "target": target,
                },
            ))

    return src, out


# Mirror of ``pipeline._DEFAULT_X26X_CRF``; we keep the constant local to
# avoid a circular import (pipeline pulls models, segmenter pulls
# pipeline). Drift between the two is guarded by
# ``tests/unit/test_target_vmaf_loop.py::test_default_crf_constants_match``.
_DEFAULT_CRF_HINT = 18


def _score_segment_vmaf(
    segment: Segment, source: Path, encoded: Path,
) -> float | None:
    """Score one encoded segment against the matching source span.

    Returns ``None`` when libvmaf isn't available or the run failed —
    the feedback loop treats ``None`` as "don't retry" rather than
    looping forever waiting for a score that will never arrive.
    """
    # We need to compare encoded against the matching span of the
    # source. Slice the source via an intermediate stream-copy so
    # libvmaf gets two identical-duration files with PTS at 0.
    import tempfile

    from yt_uniquifier.core.qa import vmaf as _vmaf

    span = max(0.001, segment.end_sec - segment.start_sec)
    with tempfile.TemporaryDirectory(prefix="vmaf_ref_") as tmp:
        ref = Path(tmp) / "ref.mkv"
        cmd = [
            ffmpeg_bin(),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{segment.start_sec:.6f}",
            "-i", str(source),
            "-t", f"{span:.6f}",
            "-c:v", "copy", "-an", "-sn",
            "-avoid_negative_ts", "make_zero",
            str(ref),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "target_vmaf: ref slice failed for seg %d: %s",
                segment.idx, exc.stderr.strip() if exc.stderr else exc,
            )
            return None
        # Subsample=4 keeps this fast on short segments without losing
        # meaningful accuracy at the 80-95 VMAF range we care about.
        result = _vmaf.compute(ref, encoded, subsample=4)
    return result.score


def parallel_safe(plan: Plan) -> int:
    """Return max concurrent encode workers safe for this plan's encoder.

    v0.3+: each EncoderCandidate carries `max_parallel` (detected at probe
    time — NVENC consumer=3, pro=8, CPU=cpu_count()//2, etc.). The
    orchestrator caps user-requested workers at this number.

    Returns >= 1 always.
    """
    return max(1, plan.encoder.max_parallel)


def _resource_budget_key(plan: Plan) -> str:
    """Group encoders that contend for the same physical resource."""
    vendor = plan.encoder.vendor
    if vendor in {"x264", "x265", "libaom", "svtav1"}:
        return "cpu"
    if vendor == "nvenc":
        visible = os.environ.get("CUDA_VISIBLE_DEVICES", "all")
        return f"nvenc:{visible}"
    if vendor == "amf":
        ordinal = os.environ.get("GPU_DEVICE_ORDINAL", "all")
        return f"amf:{ordinal}"
    return f"hardware:{vendor}"


def _resource_budget(plan: Plan) -> tuple[int, threading.BoundedSemaphore]:
    key = _resource_budget_key(plan)
    with _RESOURCE_BUDGETS_LOCK:
        existing = _RESOURCE_BUDGETS.get(key)
        if existing is not None:
            return existing
        limit = parallel_safe(plan)
        created = (limit, threading.BoundedSemaphore(limit))
        _RESOURCE_BUDGETS[key] = created
        return created


def _resource_pool_dir(plan: Plan) -> Path:
    """Return a filesystem-safe directory for one physical-resource key."""
    return resource_pool_dir(f"encoder:{_resource_budget_key(plan)}")


def _acquire_cross_process_resource(
    plan: Plan,
    *,
    cancel_token: CancelToken | None,
    on_event: Callable[[RunEvent], None] | None,
) -> RunAdmission:
    """Wait for one host-wide encoder slot, remaining cancellable."""
    limit = parallel_safe(plan)
    pool_dir = _resource_pool_dir(plan)
    owner_id = (
        f"encoder-{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(8)}"
    )
    queued_emitted = False
    while True:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError(
                "cancelled by user while waiting for cross-process encoder capacity"
            )
        try:
            return RunAdmission.acquire(pool_dir, owner_id, limit)
        except RunAdmissionFull:
            if not queued_emitted and on_event is not None:
                on_event(RunEvent(kind="log", payload={
                    "phase": "workers",
                    "message": (
                        f"waiting for cross-process {plan.encoder.vendor} capacity "
                        f"(shared limit {limit})"
                    ),
                }))
                queued_emitted = True
            if cancel_token is not None:
                cancel_token.wait(0.25)
            else:
                time.sleep(0.25)
        except RunAdmissionError as exc:
            raise PipelineError(
                "cross-process encoder admission is unavailable or has a "
                "different capacity; stop active runs and clear the configured "
                "YT_UNIQ_RESOURCE_LOCK_DIR before retrying"
            ) from exc


def _process_video_segment_with_budget(
    segment: Segment,
    plan: Plan,
    work_dir: Path,
    *,
    on_event: Callable[[RunEvent], None] | None,
    cancel_token: CancelToken | None,
    pause_token: PauseToken | None,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path | None, Path]:
    """Acquire process- and filesystem-wide slots before spawning FFmpeg."""
    limit, semaphore = _resource_budget(plan)
    queued_emitted = False
    while not semaphore.acquire(timeout=0.25):
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError("cancelled by user while waiting for encoder capacity")
        if not queued_emitted and on_event is not None:
            on_event(RunEvent(kind="log", payload={
                "phase": "workers",
                "message": (
                    f"waiting for shared {plan.encoder.vendor} capacity "
                    f"(process limit {limit})"
                ),
            }))
            queued_emitted = True
    resource_admission: RunAdmission | None = None
    try:
        resource_admission = _acquire_cross_process_resource(
            plan,
            cancel_token=cancel_token,
            on_event=on_event,
        )
        return process_video_segment(
            segment,
            plan,
            work_dir,
            on_event=on_event,
            cancel_token=cancel_token,
            pause_token=pause_token,
            extra_env=extra_env,
        )
    finally:
        if resource_admission is not None:
            resource_admission.release()
        semaphore.release()


def process_video_segments_parallel(
    pending: list[Segment],
    plan: Plan,
    work_dir: Path,
    *,
    workers: int = 1,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    pause_token: PauseToken | None = None,
    on_segment_done: Callable[[int, Path | None, Path], None] | None = None,
) -> list[tuple[int, Path | None, Path]]:
    """Run `process_video_segment` over `pending` segments concurrently.

    Falls back to sequential when workers <= 1 or the detected encoder cap is one.
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
        results: list[tuple[int, Path | None, Path]] = []
        for seg in pending:
            if cancel_token and cancel_token.is_cancelled():
                raise PipelineError("cancelled by user")
            src, out = _process_video_segment_with_budget(
                seg, plan, work_dir,
                on_event=on_event, cancel_token=cancel_token,
                pause_token=pause_token,
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
    worker_cancel = cancel_token or CancelToken()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective) as pool:
        futures = {
            pool.submit(
                _process_video_segment_with_budget, seg, plan, work_dir,
                on_event=on_event, cancel_token=worker_cancel,
                pause_token=pause_token,
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
            worker_cancel.cancel()
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
    pause_token: PauseToken | None = None,
) -> tuple[Path | None, LoudnormMeasurement | None]:
    """Process the full source's main audio. Returns (path, measurement).

    For `seed_strategy='divergent'` (v0.4.2+), audio is windowed into
    ~60 s pieces each with their own per-window seed. Otherwise audio
    runs in a single pass on the full source (legacy behavior).
    """
    out = work_dir / "main_audio.m4a"
    temp_out = work_dir / (
        f".main_audio.{os.getpid()}.{secrets.token_hex(4)}.part.m4a"
    )
    if plan.profile.seed_strategy == "divergent":
        cmd, measurement = build_main_audio_command_windowed(
            plan, temp_out, loudnorm_measurement=loudnorm_measurement
        )
    else:
        cmd, measurement = build_main_audio_command(
            plan, temp_out, loudnorm_measurement=loudnorm_measurement
        )
    if not cmd.args:
        return None, measurement
    # Defense-in-depth: re-probe rubberband availability right before
    # the audio chain runs. Closes the window between preflight and
    # runtime that burned 18 min of video work on the 2026-05-31 matrix
    # incident (see audio_windows.verify_audio_filters_available docstring).
    verify_audio_filters_available(plan)
    try:
        run_ffmpeg(
            cmd, output=temp_out, on_event=on_event, cancel_token=cancel_token,
            pause_token=pause_token,
            log_path=out.with_suffix(".m4a.log"),
        )
        if not temp_out.is_file() or temp_out.stat().st_size <= 0:
            raise PipelineError("main audio encode produced no usable output")
        os.replace(temp_out, out)
    finally:
        temp_out.unlink(missing_ok=True)
    return out, measurement


# ---- concat ------------------------------------------------------------------

def concat_segments(
    video_segments: list[Path],
    main_audio: Path | None,
    output: Path,
    metadata_args: list[str],
    *,
    work_dir: Path,
    media_source: Path | None = None,
    map_chapters_from: Path | None = None,
    audio_passthrough_count: int = 0,
    audio_source_indices: list[int] | None = None,
    audio_streams: list[AudioStream] | None = None,
    subtitle_codecs: list[str] | None = None,
    auxiliary_streams: list[AuxiliaryStream] | None = None,
    target_duration_sec: float | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
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
    input_indices: dict[Path, int] = {}
    next_input_idx = 1

    def add_input(path: Path | None) -> int | None:
        nonlocal next_input_idx
        if path is None:
            return None
        key = path.resolve(strict=False)
        existing = input_indices.get(key)
        if existing is not None:
            return existing
        idx = next_input_idx
        next_input_idx += 1
        input_indices[key] = idx
        cmd.extend(["-i", str(path)])
        return idx

    main_audio_idx = add_input(main_audio)
    media_idx = add_input(media_source)
    chapter_idx = add_input(map_chapters_from)

    auxiliary_streams = auxiliary_streams or []
    unsupported_auxiliary = unsupported_auxiliary_streams(
        auxiliary_streams, output.suffix.lower().lstrip("."),
    )
    if unsupported_auxiliary:
        descriptions = ", ".join(
            stream.codec_tag or stream.codec or stream.kind
            for stream in unsupported_auxiliary
        )
        raise PipelineError(
            f"auxiliary stream(s) [{descriptions}] cannot be preserved in "
            f"{output.suffix.lower() or 'the selected container'}"
        )
    if auxiliary_streams and media_idx is None:
        raise PipelineError("auxiliary streams require the original media source")

    cmd += ["-map", "0:v:0"]
    selected_audio = (
        audio_source_indices
        if audio_source_indices is not None
        else list(range(audio_passthrough_count + 1))
    )
    if main_audio_idx is not None:
        cmd += ["-map", f"{main_audio_idx}:a:0"]
    elif selected_audio:
        source_idx = media_idx if media_idx is not None else 0
        cmd += ["-map", f"{source_idx}:a:{selected_audio[0]}?"]
    passthrough_idx = media_idx if media_idx is not None else 0
    for relative_idx in selected_audio[1:]:
        cmd += ["-map", f"{passthrough_idx}:a:{relative_idx}?"]
    subtitle_idx = media_idx if media_idx is not None else 0
    cmd += ["-map", f"{subtitle_idx}:s?"]
    attachments = [stream for stream in auxiliary_streams if stream.kind == "attachment"]
    data_streams = [stream for stream in auxiliary_streams if stream.kind == "data"]
    attached_pictures = [
        stream for stream in auxiliary_streams if stream.kind == "attached_pic"
    ]
    for auxiliary in attachments:
        cmd += ["-map", f"{media_idx}:{auxiliary.index}"]
    if attachments:
        cmd += ["-c:t", "copy"]
    for auxiliary in data_streams:
        cmd += ["-map", f"{media_idx}:{auxiliary.index}"]
    if data_streams:
        cmd += ["-c:d", "copy"]
    for auxiliary in attached_pictures:
        cmd += ["-map", f"{media_idx}:{auxiliary.index}"]

    output_container = output.suffix.lower()
    image_subtitle_codecs = {
        "dvb_subtitle", "dvd_subtitle", "hdmv_pgs_subtitle", "pgs", "xsub",
    }
    normalized_subtitle_codecs = {codec.lower() for codec in subtitle_codecs or []}
    if output_container in {".mp4", ".mov", ".m4v"}:
        incompatible = normalized_subtitle_codecs & image_subtitle_codecs
        if incompatible:
            names = ", ".join(sorted(incompatible))
            raise PipelineError(
                f"cannot mux image-based subtitle codec(s) {names} into "
                f"{output_container}; use MKV or convert subtitles first"
            )
        subtitle_args = ["-c:s", "mov_text"]
    else:
        subtitle_args = ["-c:s", "copy"]
    cmd += ["-c:v", "copy"]
    if main_audio_idx is not None or selected_audio:
        if audio_streams is None:
            cmd += ["-c:a", "copy"]
        else:
            compatible_mp4_audio = {"aac", "ac3", "eac3", "alac", "mp3"}
            for output_idx, stream in enumerate(audio_streams):
                codec = stream.codec.lower()
                should_copy = (
                    output_container not in {".mp4", ".mov", ".m4v"}
                    or codec in compatible_mp4_audio
                    or (main_audio_idx is not None and output_idx == 0)
                )
                if should_copy:
                    cmd += [f"-c:a:{output_idx}", "copy"]
                else:
                    bitrate = "128k" if stream.channels <= 1 else (
                        "384k" if stream.channels == 2 else "512k"
                    )
                    cmd += [
                        f"-c:a:{output_idx}", "aac",
                        f"-b:a:{output_idx}", bitrate,
                        f"-ar:a:{output_idx}", "48000",
                    ]
    else:
        cmd += ["-an"]
    cmd += subtitle_args
    if chapter_idx is not None:
        cmd += ["-map_chapters", str(chapter_idx)]
    else:
        cmd += ["-map_chapters", "-1"]
    if output_container in {".mp4", ".mov", ".m4v"}:
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
    for index, auxiliary in enumerate(attachments):
        for key, value in (
            ("filename", auxiliary.filename),
            ("mimetype", auxiliary.mimetype),
            ("title", auxiliary.title),
        ):
            if value:
                cmd += [f"-metadata:s:t:{index}", f"{key}={value}"]
    for index, auxiliary in enumerate(data_streams):
        for key, value in (
            ("language", auxiliary.language),
            ("handler_name", auxiliary.title),
            ("timecode", auxiliary.timecode),
        ):
            if value:
                cmd += [f"-metadata:s:d:{index}", f"{key}={value}"]
    for index, auxiliary in enumerate(attached_pictures, start=1):
        cmd += [f"-disposition:v:{index}", "attached_pic"]
        if auxiliary.title:
            cmd += [f"-metadata:s:v:{index}", f"title={auxiliary.title}"]
    tmp_output = output.with_name(
        f".{output.stem}.{os.getpid()}.{secrets.token_hex(4)}.part{output.suffix}"
    )
    cmd += [str(tmp_output)]

    concat_log = work_dir / "concat.log"
    try:
        run_ffmpeg(
            BuiltCommand(args=cmd),
            output=tmp_output,
            on_event=on_event,
            cancel_token=cancel_token,
            log_path=concat_log,
        )
    except Exception as exc:
        tmp_output.unlink(missing_ok=True)
        # The shared runner bounds in-memory logs and tees the complete stream
        # to concat.log. Preserve a useful head+tail diagnostic without
        # retaining unbounded FFmpeg output in Python memory.
        try:
            full = concat_log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            full = ""
        head = full[:300]
        tail = full[-500:] if len(full) > 800 else ""
        snippet = head + ("\n…\n" + tail if tail else "") if full else str(exc)
        raise PipelineError(f"concat failed: {snippet}") from exc
    except BaseException:
        tmp_output.unlink(missing_ok=True)
        raise

    if not tmp_output.exists() or tmp_output.stat().st_size == 0:
        tmp_output.unlink(missing_ok=True)
        raise PipelineError("concat reported success but produced no output")
    try:
        os.replace(tmp_output, output)
    except OSError as exc:
        tmp_output.unlink(missing_ok=True)
        raise PipelineError(f"could not publish final output {output}: {exc}") from exc
