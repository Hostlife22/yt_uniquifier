"""Deterministic calibration for authorized source/derivative diagnostics.

The engine searches one existing profile's intensity scale. It does not create a
second processing pipeline and its similarity score is not a prediction of an
external rights system. Calibration v2 uses a content-stratified probe, fixed
random draws, bounded non-monotone search, and durable scored-trial caching.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.calibration.intensity import scale_profile
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Profile, Segment
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.qa.cid_predict import predict
from yt_uniquifier.core.qa.quality import QualityMetric, quality_score
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.segmenter import stream_copy_extract
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

CalibrationMetric = Literal["chromaprint", "sscd"]

# Bump whenever cached score semantics or the probe construction changes.
CALIBRATION_CACHE_SCHEMA_VERSION = 2
_PROBE_WINDOW_COUNT = 3
_MIN_LOG_INTERVAL = 0.025

# Public evaluator contract: given (source, candidate, cancel_token), return
# a similarity score in [0, 1] where higher means closer under the local metric.
MetricEvaluator = Callable[[Path, Path, "CancelToken | None"], float]


@dataclass(frozen=True)
class CalibrationTarget:
    max_self_match: float = 0.2
    # The threshold is interpreted against the explicitly reported backend.
    # VMAF and SSIM x 100 are not interchangeable; calibration pins whichever
    # backend succeeds first and aborts if it changes during the same search.
    min_quality: float = 88.0
    max_iterations: int = 5
    test_clip_sec: float = 60.0
    # Every candidate uses the same stochastic realization (common random
    # numbers), making factor comparisons reproducible.
    seed: int = 0
    # Bounded search prevents an unproductive metric from escalating effects
    # indefinitely. Bounds are additive API fields and preserve old callers.
    min_factor: float = 0.25
    max_factor: float = 4.0


@dataclass(frozen=True)
class CalibrationStep:
    iteration: int
    intensity_factor: float
    profile: Profile
    self_match: float
    quality: float | None
    quality_metric: QualityMetric | None
    duration_sec: float
    note: str | None = None


@dataclass(frozen=True)
class CalibratedResult:
    profile: Profile
    factor: float
    steps: list[CalibrationStep]
    converged: bool
    final_self_match: float
    final_quality: float | None
    final_quality_metric: QualityMetric | None
    note: str | None = None


@dataclass(frozen=True)
class _CachedTrial:
    self_match: float
    quality: float
    quality_metric: QualityMetric
    quality_note: str | None


ProgressFn = Callable[[CalibrationStep], None]


def calibrate(
    input_path: Path,
    base_profile: Profile,
    target: CalibrationTarget,
    *,
    work_dir: Path,
    encoder_override: str | None = None,
    on_step: ProgressFn | None = None,
    cancel_token: CancelToken | None = None,
    metric: CalibrationMetric = "chromaprint",
    evaluator: MetricEvaluator | None = None,
) -> CalibratedResult:
    """Search bounded profile intensity against independent constraints.

    The first three trials (subject to ``max_iterations``) are the existing
    profile factor, the lower bound, and the upper bound. Later trials split
    the most informative logarithmic interval. This preserves useful
    bracketing for monotone objectives while still sampling non-monotone ones.

    A custom ``evaluator`` is a test/extension seam and intentionally disables
    durable score reuse because its implementation has no stable cache identity.
    """
    _validate_target(target)
    eval_fn = evaluator if evaluator is not None else _build_evaluator(metric)

    work_dir.mkdir(parents=True, exist_ok=True)
    clip = _cut_test_clip(input_path, work_dir, target.test_clip_sec)

    steps: list[CalibrationStep] = []
    observed_quality_metric: QualityMetric | None = None
    use_trial_cache = evaluator is None and clip.is_file()
    plan_hashes_seen: set[str] = set()
    # Concurrent CLI/GUI calibrations may share the same durable cache. Keep
    # incomplete runner/output artifacts session-local; only a complete score
    # is published into the shared atomic cache.
    session_id = f"{os.getpid()}_{secrets.token_hex(4)}"

    while len(steps) < target.max_iterations:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError("calibration cancelled by user")

        factor = _next_factor(steps, target)
        if factor is None:
            break
        started = time.monotonic()
        scaled = scale_profile(base_profile, factor).model_copy(update={
            "seed_strategy": "fixed",
            "seed": target.seed,
        })
        plan = build_plan(clip, scaled, encoder_override)
        iteration = len(steps) + 1

        cached = (
            _load_trial(work_dir, plan.plan_hash, metric)
            if use_trial_cache else None
        )
        if cached is not None:
            self_match = cached.self_match
            q_value = cached.quality
            q_metric = cached.quality_metric
            q_note = _join_notes("scored-trial cache hit", cached.quality_note)
        else:
            out_path = work_dir / (
                f"trial_{plan.plan_hash}_{session_id}.{scaled.output_container}"
            )
            step_work = work_dir / "work" / plan.plan_hash / session_id
            self_match, q_value, q_metric, q_note = _run_trial(
                clip=clip,
                plan=plan,
                out_path=out_path,
                step_work=step_work,
                encoder_override=encoder_override,
                eval_fn=eval_fn,
                cancel_token=cancel_token,
                iteration=iteration,
                factor=factor,
            )
            if use_trial_cache:
                _save_trial(
                    work_dir,
                    plan.plan_hash,
                    metric,
                    _CachedTrial(self_match, q_value, q_metric, q_note),
                )

        if observed_quality_metric is None:
            observed_quality_metric = q_metric
        elif q_metric != observed_quality_metric:
            raise PipelineError(
                "quality metric changed during calibration "
                f"({observed_quality_metric} -> {q_metric}); scores are not "
                "comparable, so the search was aborted"
            )

        repeated_profile = plan.plan_hash in plan_hashes_seen
        plan_hashes_seen.add(plan.plan_hash)
        if repeated_profile:
            q_note = _join_notes(q_note, "scaled profile reached a parameter plateau")

        step = _step(
            iteration,
            factor,
            scaled,
            cid_self=self_match,
            quality=q_value,
            quality_metric=q_metric,
            duration_sec=time.monotonic() - started,
            note=q_note,
        )
        steps.append(step)
        if on_step:
            on_step(step)

    if not steps:
        raise PipelineError("calibration produced no steps")

    best = min(steps, key=lambda step: _rank(step, target))
    converged = _is_feasible(best, target)
    stop_reason = (
        "search interval plateaued"
        if len(steps) < target.max_iterations
        else "search budget exhausted"
    )
    if converged:
        note = (
            f"feasible candidate selected after {len(steps)} bounded trial(s); "
            f"{stop_reason}; verify the tuned profile on the full source"
        )
    else:
        note = (
            f"no candidate satisfied both constraints after {len(steps)} trial(s); "
            f"{stop_reason}; returning lowest-violation candidate"
        )

    return CalibratedResult(
        profile=best.profile,
        factor=best.intensity_factor,
        steps=steps,
        converged=converged,
        final_self_match=best.self_match,
        final_quality=best.quality,
        final_quality_metric=best.quality_metric,
        note=note,
    )


def _run_trial(
    *,
    clip: Path,
    plan: Plan,
    out_path: Path,
    step_work: Path,
    encoder_override: str | None,
    eval_fn: MetricEvaluator,
    cancel_token: CancelToken | None,
    iteration: int,
    factor: float,
) -> tuple[float, float, QualityMetric, str | None]:
    """Encode and score one factor; retry infrastructure failure in place."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            run_full(
                plan,
                RunOptions(
                    work_dir=step_work,
                    output=out_path,
                    encoder_override=encoder_override,
                    enforce_preflight=False,
                    keep_segments=False,
                    force_new_variant=attempt > 0,
                ),
                cancel_token=cancel_token,
            )
            self_match = eval_fn(clip, out_path, cancel_token)
            if not math.isfinite(self_match) or not 0.0 <= self_match <= 1.0:
                raise PipelineError(
                    f"calibration evaluator returned invalid score: {self_match!r}"
                )
            quality = quality_score(clip, out_path)
            if (
                not math.isfinite(quality.value)
                or not 0.0 <= quality.value <= 100.0
            ):
                raise PipelineError(
                    f"quality evaluator returned invalid score: {quality.value!r}"
                )
            return self_match, quality.value, quality.metric, quality.note
        except Exception as exc:  # noqa: PERF203 - retry boundary is intentional
            if cancel_token is not None and cancel_token.is_cancelled():
                raise
            last_error = exc
    assert last_error is not None
    raise PipelineError(
        f"calibration iteration {iteration} failed twice at factor "
        f"{factor:g}: {last_error}"
    ) from last_error


def _validate_target(target: CalibrationTarget) -> None:
    if not 0.0 <= target.max_self_match <= 1.0:
        raise PipelineError("max_self_match must be in [0, 1]")
    if not 0.0 <= target.min_quality <= 100.0:
        raise PipelineError("min_quality must be in [0, 100]")
    if not 1 <= target.max_iterations <= 64:
        raise PipelineError("max_iterations must be in [1, 64]")
    if not math.isfinite(target.test_clip_sec) or target.test_clip_sec <= 0.0:
        raise PipelineError("test_clip_sec must be a positive finite number")
    if (
        not math.isfinite(target.min_factor)
        or not math.isfinite(target.max_factor)
        or target.min_factor <= 0.0
        or target.max_factor < 1.0
        or target.min_factor > 1.0
        or target.min_factor >= target.max_factor
    ):
        raise PipelineError(
            "calibration factor bounds must satisfy 0 < min_factor <= 1 "
            "<= max_factor and min_factor < max_factor"
        )


def _step(
    i: int,
    factor: float,
    profile: Profile,
    *,
    cid_self: float,
    quality: float | None,
    quality_metric: QualityMetric | None,
    duration_sec: float,
    note: str | None,
) -> CalibrationStep:
    return CalibrationStep(
        iteration=i,
        intensity_factor=factor,
        profile=profile,
        self_match=cid_self,
        quality=quality,
        quality_metric=quality_metric,
        duration_sec=duration_sec,
        note=note,
    )


def _is_feasible(step: CalibrationStep, target: CalibrationTarget) -> bool:
    return (
        step.self_match <= target.max_self_match
        and step.quality is not None
        and step.quality >= target.min_quality
    )


def _violation(
    step: CalibrationStep, target: CalibrationTarget,
) -> tuple[float, float]:
    similarity_scale = max(target.max_self_match, 0.05)
    quality_scale = max(target.min_quality, 1.0)
    similarity = max(0.0, step.self_match - target.max_self_match) / similarity_scale
    quality_value = step.quality if step.quality is not None else 0.0
    quality = max(0.0, target.min_quality - quality_value) / quality_scale
    return similarity, quality


def _rank(step: CalibrationStep, target: CalibrationTarget) -> tuple[float, ...]:
    """Feasibility-first/Pareto-inspired deterministic result ordering."""
    similarity_violation, quality_violation = _violation(step, target)
    quality_value = step.quality if step.quality is not None else -math.inf
    if _is_feasible(step, target):
        # Once both gates pass, preserve quality, then prefer the gentler
        # profile. The old code instead minimized similarity even if that
        # selected a visibly worse feasible candidate.
        return (0.0, -quality_value, step.intensity_factor, step.self_match)
    return (
        1.0,
        max(similarity_violation, quality_violation),
        similarity_violation + quality_violation,
        -quality_value,
        step.intensity_factor,
    )


def _constraint_state(
    step: CalibrationStep, target: CalibrationTarget,
) -> tuple[bool, bool]:
    return (
        step.self_match <= target.max_self_match,
        step.quality is not None and step.quality >= target.min_quality,
    )


def _next_factor(
    steps: list[CalibrationStep], target: CalibrationTarget,
) -> float | None:
    """Return the next bounded factor without assuming metric monotonicity."""
    anchors = _deduplicate_factors([1.0, target.min_factor, target.max_factor])
    used = [step.intensity_factor for step in steps]
    for anchor in anchors:
        if not _factor_seen(anchor, used):
            return anchor

    ordered = sorted(steps, key=lambda step: step.intensity_factor)
    candidates: list[tuple[tuple[float, ...], float]] = []
    for left, right in zip(ordered, ordered[1:], strict=False):
        log_width = math.log(right.intensity_factor / left.intensity_factor)
        if log_width <= _MIN_LOG_INTERVAL:
            continue
        midpoint = math.sqrt(left.intensity_factor * right.intensity_factor)
        if _factor_seen(midpoint, used):
            continue
        left_state = _constraint_state(left, target)
        right_state = _constraint_state(right, target)
        feasibility_transition = float(
            _is_feasible(left, target) != _is_feasible(right, target)
        )
        constraint_transition = float(left_state != right_state)
        endpoint_violation = min(
            sum(_violation(left, target)),
            sum(_violation(right, target)),
        )
        # First refine a feasibility boundary, then a change in the active
        # constraint, then cover wide/near-feasible intervals. The factor
        # tiebreak makes identical runs choose the same candidate.
        priority = (
            feasibility_transition,
            constraint_transition,
            log_width,
            -endpoint_violation,
            -midpoint,
        )
        candidates.append((priority, midpoint))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _deduplicate_factors(factors: list[float]) -> list[float]:
    out: list[float] = []
    for factor in factors:
        if not _factor_seen(factor, out):
            out.append(factor)
    return out


def _factor_seen(factor: float, factors: list[float]) -> bool:
    return any(
        math.isclose(factor, seen, rel_tol=1e-12, abs_tol=1e-12)
        for seen in factors
    )


def _cut_test_clip(source: Path, work_dir: Path, secs: float) -> Path:
    """Build a content-keyed start/middle/end probe within one time budget."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(_source_fingerprint(source))
    digest.update(
        f"schema={CALIBRATION_CACHE_SCHEMA_VERSION};seconds={secs:.6f}".encode()
    )
    cache_key = digest.hexdigest()
    dest = work_dir / f"test_clip_{cache_key}.mkv"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    meta = probe_file(source)
    windows = _stratified_windows(meta.duration_sec, secs)
    if not windows:
        raise PipelineError("calibration source has no positive-duration probe window")

    unique = f"{os.getpid()}_{secrets.token_hex(4)}"
    temp_dest = work_dir / f".{dest.stem}.{unique}.part.mkv"
    parts_dir = work_dir / f".probe_parts_{cache_key}_{unique}"
    try:
        if len(windows) == 1:
            start, end = windows[0]
            stream_copy_extract(
                Segment(idx=0, start_sec=start, end_sec=end), source, temp_dest
            )
        else:
            parts_dir.mkdir(parents=True, exist_ok=False)
            parts: list[Path] = []
            for idx, (start, end) in enumerate(windows):
                part = parts_dir / f"part_{idx:02d}.mkv"
                stream_copy_extract(
                    Segment(idx=idx, start_sec=start, end_sec=end), source, part
                )
                parts.append(part)
            _concat_probe_parts(parts, temp_dest, parts_dir)

        if not temp_dest.is_file() or temp_dest.stat().st_size <= 0:
            raise PipelineError("calibration probe extraction produced no usable output")
        extracted = probe_file(temp_dest)
        if extracted.duration_sec <= 0.0 or not extracted.video:
            raise PipelineError("calibration probe is missing decodable video")
        os.replace(temp_dest, dest)
    finally:
        temp_dest.unlink(missing_ok=True)
        if parts_dir.exists():
            shutil.rmtree(parts_dir, ignore_errors=True)
    return dest


def _source_fingerprint(source: Path) -> bytes:
    stat = source.stat()
    digest = hashlib.blake2b(digest_size=20)
    digest.update(stat.st_size.to_bytes(16, "big", signed=False))
    with source.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return digest.digest()


def _stratified_windows(duration: float, budget: float) -> list[tuple[float, float]]:
    """Return non-overlapping start/middle/end windows totalling at most budget."""
    if not math.isfinite(duration) or duration <= 0.0:
        return [(0.0, budget)]
    total = min(duration, budget)
    if total <= 0.0:
        return []
    if duration <= budget or total < float(_PROBE_WINDOW_COUNT):
        return [(0.0, total)]

    width = total / _PROBE_WINDOW_COUNT
    starts = (0.0, (duration - width) / 2.0, duration - width)
    return [(start, min(duration, start + width)) for start in starts]


def _concat_probe_parts(parts: list[Path], destination: Path, work_dir: Path) -> None:
    manifest = work_dir / "concat.txt"
    quote = "'"
    escaped_quote = "'\\''"
    lines = [
        f"file '{str(part.absolute()).replace(quote, escaped_quote)}'"
        for part in parts
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(destination),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"calibration probe concat failed: {exc.stderr.strip()}"
        ) from exc


def _trial_cache_path(work_dir: Path, plan_hash: str, metric: CalibrationMetric) -> Path:
    return work_dir / "trial_cache" / f"{plan_hash}_{metric}.json"


def _load_trial(
    work_dir: Path, plan_hash: str, metric: CalibrationMetric,
) -> _CachedTrial | None:
    path = _trial_cache_path(work_dir, plan_hash, metric)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != CALIBRATION_CACHE_SCHEMA_VERSION:
            return None
        if raw.get("plan_hash") != plan_hash or raw.get("metric") != metric:
            return None
        self_match = float(raw["self_match"])
        quality = float(raw["quality"])
        quality_metric = raw["quality_metric"]
        if quality_metric not in ("vmaf", "ssim"):
            return None
        if (
            not math.isfinite(self_match)
            or not 0.0 <= self_match <= 1.0
            or not math.isfinite(quality)
            or not 0.0 <= quality <= 100.0
        ):
            return None
        quality_note = raw.get("quality_note")
        return _CachedTrial(
            self_match=self_match,
            quality=quality,
            quality_metric=quality_metric,
            quality_note=str(quality_note) if quality_note is not None else None,
        )
    except (
        FileNotFoundError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _save_trial(
    work_dir: Path,
    plan_hash: str,
    metric: CalibrationMetric,
    trial: _CachedTrial,
) -> None:
    path = _trial_cache_path(work_dir, plan_hash, metric)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CALIBRATION_CACHE_SCHEMA_VERSION,
        "plan_hash": plan_hash,
        "metric": metric,
        "self_match": trial.self_match,
        "quality": trial.quality,
        "quality_metric": trial.quality_metric,
        "quality_note": trial.quality_note,
    }
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.01 * (2 ** attempt), 0.1))
    finally:
        temp.unlink(missing_ok=True)


def _join_notes(*notes: str | None) -> str | None:
    present = [note for note in notes if note]
    return "; ".join(present) if present else None


def _build_evaluator(metric: CalibrationMetric) -> MetricEvaluator:
    if metric == "chromaprint":
        return _evaluate_chromaprint
    if metric == "sscd":
        return _evaluate_sscd
    raise PipelineError(
        f"unknown calibration metric: {metric!r} "
        "(expected one of: chromaprint, sscd)"
    )


def _evaluate_chromaprint(
    source: Path, candidate: Path, cancel: CancelToken | None,
) -> float:
    """Return the legacy weighted local self-similarity heuristic."""
    _ = cancel
    return predict(source, candidate).match_probability_self


def _evaluate_sscd(
    source: Path, candidate: Path, cancel: CancelToken | None,
) -> float:
    """Return direct SSCD mean cosine, clamped to the public range."""
    from yt_uniquifier.core.qa.sscd import compute_sscd

    result = compute_sscd(source, candidate, cancel_token=cancel)
    return max(0.0, min(1.0, result.mean_similarity))


# Avoid mypy false-positive on the public Plan annotation imported for _run_trial.
_ = (Plan,)
