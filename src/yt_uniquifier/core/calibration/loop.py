"""Iterative calibration against local quality and self-similarity diagnostics.

For a given owned/licensed source and base profile, find an ``intensity_factor``
that satisfies a local diagnostic target without violating perceptual quality.
The similarity objective is not a prediction of an external rights system.

To stay fast, each iteration encodes only the first `test_clip_sec` of the
source (via stream-copy cut). The final tuned profile can then be applied
to the full file in one regular `yt-uniq run`.

Bisect strategy:
  - start at factor=1.0
  - if self_match > target_max → factor *= 1.5 (more aggressive)
  - elif vmaf < min_vmaf      → factor /= 1.3 (back off)
  - else → converged
  - track best-so-far: lowest self_match among iterations whose VMAF passed,
    or just lowest self_match if none passed
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.calibration.intensity import scale_profile
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Profile, Segment
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.qa.cid_predict import predict
from yt_uniquifier.core.qa.quality import QualityMetric, quality_score
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.segmenter import stream_copy_extract

CalibrationMetric = Literal["chromaprint", "sscd"]

# Public evaluator contract: given (source, candidate, cancel_token), return
# a similarity score in [0, 1] where higher = more similar to the source
# (i.e. closer under this local metric). Calibration drives this diagnostic
# toward ``target.max_self_match`` while enforcing quality independently.
MetricEvaluator = Callable[[Path, Path, "CancelToken | None"], float]


@dataclass(frozen=True)
class CalibrationTarget:
    max_self_match: float = 0.2
    # 0..100 on the unified scale (VMAF, SSIM × 100, or pHash × 100). 88
    # is a sane VMAF target on natural footage; SSIM-fallback would treat
    # the same number as a stricter bar.
    min_quality: float = 88.0
    max_iterations: int = 5
    test_clip_sec: float = 60.0
    # Every candidate uses the same stochastic realization. Otherwise a
    # per-run seed changes both intensity and random pattern between steps,
    # making the objective noisy and non-reproducible.
    seed: int = 0


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
    """Bisect intensity_factor on a short clip until target is met.

    A6 (v0.5.5): ``cancel_token`` is honoured at iteration boundaries
    AND inside the inner ``run_full`` (it is forwarded). The previous
    GUI ``CalibrateWorker.request_cancel()`` silently no-op'd because
    this function had no cancel surface; clicking Cancel in the
    Calibrate screen would leave encodes running for minutes.

    v0.8.0 R6: ``metric`` selects the local similarity diagnostic.
    ``"chromaprint"`` retains the legacy weighted field and ``"sscd"``
    uses mean representation cosine. Neither predicts an external system.

    The ``evaluator`` kwarg is a test seam — pass a callable matching
    ``MetricEvaluator`` to bypass real model loading. In production
    callers should leave it ``None`` and use ``metric``.
    """
    eval_fn = evaluator if evaluator is not None else _build_evaluator(metric)

    work_dir.mkdir(parents=True, exist_ok=True)
    clip = _cut_test_clip(input_path, work_dir, target.test_clip_sec)

    steps: list[CalibrationStep] = []
    factor = 1.0
    lower_factor: float | None = None
    upper_factor: float | None = None
    best: CalibrationStep | None = None
    converged = False

    for i in range(1, target.max_iterations + 1):
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError("calibration cancelled by user")
        scaled = scale_profile(base_profile, factor).model_copy(update={
            "seed_strategy": "fixed",
            "seed": target.seed,
        })
        out_path = work_dir / f"iter_{i:02d}.{scaled.output_container}"
        step_work = work_dir / f"work_{i:02d}"

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                plan = build_plan(clip, scaled, encoder_override)
                run_full(plan, RunOptions(
                    work_dir=step_work / plan.plan_hash,
                    output=out_path,
                    encoder_override=encoder_override,
                    enforce_preflight=False,
                    keep_segments=False,
                    force_new_variant=attempt > 0,
                ), cancel_token=cancel_token)
                self_match = eval_fn(clip, out_path, cancel_token)
                q = quality_score(clip, out_path)
                q_value = q.value
                q_metric = q.metric
                q_note = q.note
                last_error = None
                break
            except Exception as exc:  # noqa: PERF203 - retry boundary is intentional
                if cancel_token is not None and cancel_token.is_cancelled():
                    raise
                last_error = exc
        if last_error is not None:
            raise PipelineError(
                f"calibration iteration {i} failed twice at factor "
                f"{factor:g}: {last_error}"
            ) from last_error

        step = _step(i, factor, scaled,
                     cid_self=self_match,
                     quality=q_value, quality_metric=q_metric,
                     note=q_note)
        steps.append(step)
        if on_step:
            on_step(step)
        best = _better(best, step, target)

        if self_match <= target.max_self_match and \
                q_value >= target.min_quality:
            converged = True
            break

        if self_match > target.max_self_match:
            lower_factor = factor
            factor = (
                (lower_factor + upper_factor) / 2
                if upper_factor is not None
                else factor * 1.5
            )
        elif q_value < target.min_quality:
            upper_factor = factor
            factor = (
                (lower_factor + upper_factor) / 2
                if lower_factor is not None
                else factor / 1.3
            )
        else:
            converged = True
            break

    if best is None and steps:
        best = steps[-1]
    if best is None:
        raise PipelineError("calibration produced no steps")

    return CalibratedResult(
        profile=best.profile,
        factor=best.intensity_factor,
        steps=steps,
        converged=converged,
        final_self_match=best.self_match,
        final_quality=best.quality,
        final_quality_metric=best.quality_metric,
        note=None if converged
            else "did not converge within max_iterations; returning best-so-far",
    )


def _step(i: int, factor: float, profile: Profile, *,
          cid_self: float, quality: float | None,
          quality_metric: QualityMetric | None,
          note: str | None) -> CalibrationStep:
    return CalibrationStep(
        iteration=i, intensity_factor=factor, profile=profile,
        self_match=cid_self, quality=quality,
        quality_metric=quality_metric, duration_sec=0.0, note=note,
    )


def _better(
    current: CalibrationStep | None, candidate: CalibrationStep,
    target: CalibrationTarget,
) -> CalibrationStep:
    """Keep the step closest to (self_match <= target, quality >= min).

    quality=None means VMAF/SSIM/pHash all failed for this step — most
    often because the output was corrupt or zero-length. Treating that
    as "passed the quality gate" let the loop converge on a broken
    candidate and report converged=True with final_quality=None.
    """
    if current is None:
        return candidate
    c_ok = (
        candidate.self_match <= target.max_self_match
        and candidate.quality is not None
        and candidate.quality >= target.min_quality
    )
    cur_ok = (
        current.self_match <= target.max_self_match
        and current.quality is not None
        and current.quality >= target.min_quality
    )
    if c_ok and not cur_ok:
        return candidate
    if cur_ok and not c_ok:
        return current
    # Both satisfy or both don't — pick lower self_match; tiebreak on higher quality.
    if candidate.self_match < current.self_match:
        return candidate
    if candidate.self_match == current.self_match and \
            (candidate.quality or 0) > (current.quality or 0):
        return candidate
    return current


def _cut_test_clip(source: Path, work_dir: Path, secs: float) -> Path:
    """Stream-copy first `secs` of source into work_dir for fast iterations."""
    stat = source.stat()
    digest = hashlib.blake2b(digest_size=16)
    digest.update(stat.st_size.to_bytes(16, "big", signed=False))
    with source.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    digest.update(f"{secs:.6f}".encode())
    dest = work_dir / f"test_clip_{digest.hexdigest()}.mkv"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    stream_copy_extract(
        Segment(idx=0, start_sec=0.0, end_sec=secs),
        source,
        dest,
    )
    return dest


def _build_evaluator(metric: CalibrationMetric) -> MetricEvaluator:
    """Pick a concrete evaluator for the requested metric.

    Lookup is dispatched on the string at call time rather than at
    import time so the SSCD branch never imports torch unless the
    caller asks for it. The chromaprint path is unconditional in v0.5+
    (fpcalc presence is a runtime check inside `predict`), so the
    closure is essentially free.
    """
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
    _ = cancel  # predict() is not cancel-aware; loop honours it between iters
    return predict(source, candidate).match_probability_self


def _evaluate_sscd(
    source: Path, candidate: Path, cancel: CancelToken | None,
) -> float:
    """Return SSCD mean cosine as the evaluator's direct similarity score.

    SSCD's mean cosine is 1.0 for identical content and trends to ~0 for
    unrelated material.  The old ``1 - similarity`` mapping inverted the
    metric and made an identical pair look maximally different.
    """
    # Lazy import — torch is in the optional ``[ml]`` extra.
    from yt_uniquifier.core.qa.sscd import compute_sscd

    result = compute_sscd(source, candidate, cancel_token=cancel)
    return max(0.0, min(1.0, result.mean_similarity))


# Avoid mypy false-positive on the unused-import that we explicitly need.
_ = (Plan,)
