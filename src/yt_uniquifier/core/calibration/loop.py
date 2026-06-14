"""Iterative calibration: tune profile intensity to hit a self-match target.

For a given source + base profile, find an `intensity_factor` that drops
predicted Content ID self-match below a threshold without crashing VMAF.

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
# (i.e. higher CID-collision risk). Calibration drives this score DOWN
# toward `target.max_self_match`.
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

    v0.8.0 R6: ``metric`` selects which similarity surrogate drives the
    bisection. ``"chromaprint"`` (default) keeps the v0.5 behaviour —
    audio-fingerprint-derived ``predict().match_probability_self``.
    ``"sscd"`` swaps in the SSCD copy-detection embedding (Meta's
    VSC2022 model); the inverted mean cosine (``1 - mean_similarity``)
    is treated as the CID-collision proxy on the same 0..1 scale, so
    ``target.max_self_match`` semantics carry over.

    The ``evaluator`` kwarg is a test seam — pass a callable matching
    ``MetricEvaluator`` to bypass real model loading. In production
    callers should leave it ``None`` and use ``metric``.
    """
    eval_fn = evaluator if evaluator is not None else _build_evaluator(metric)

    work_dir.mkdir(parents=True, exist_ok=True)
    clip = _cut_test_clip(input_path, work_dir, target.test_clip_sec)

    steps: list[CalibrationStep] = []
    factor = 1.0
    best: CalibrationStep | None = None
    converged = False

    for i in range(1, target.max_iterations + 1):
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError("calibration cancelled by user")
        scaled = scale_profile(base_profile, factor)
        out_path = work_dir / f"iter_{i:02d}.mp4"
        step_work = work_dir / f"work_{i:02d}"

        try:
            plan = build_plan(clip, scaled, encoder_override)
            run_full(plan, RunOptions(
                work_dir=step_work / plan.plan_hash,
                output=out_path,
                encoder_override=encoder_override,
                enforce_preflight=False,
                keep_segments=False,
                force_new_variant=True,
            ), cancel_token=cancel_token)
            self_match = eval_fn(clip, out_path, cancel_token)
            q = quality_score(clip, out_path)
            q_value = q.value
            q_metric = q.metric
            q_note = q.note
        except Exception as exc:
            # A6 fix: re-raise on cancel instead of swallowing as
            # "iteration failed". Pre-fix the inner run_full would
            # raise PipelineError("cancelled by user") and this
            # except block converted it into a "try harder" iteration,
            # so calibration kept encoding after the user clicked
            # Cancel.
            if cancel_token is not None and cancel_token.is_cancelled():
                raise
            step = _step(i, factor, scaled, cid_self=1.0,
                         quality=None, quality_metric=None,
                         note=f"iteration failed: {exc}")
            steps.append(step)
            if on_step:
                on_step(step)
            factor *= 1.5  # try harder
            continue

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
            factor *= 1.5
        elif q_value < target.min_quality:
            factor /= 1.3
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
    dest = work_dir / "test_clip.mp4"
    if dest.exists():
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
    """Default v0.5 behaviour — predicted self-match from `cid_predict`."""
    _ = cancel  # predict() is not cancel-aware; loop honours it between iters
    return predict(source, candidate).match_probability_self


def _evaluate_sscd(
    source: Path, candidate: Path, cancel: CancelToken | None,
) -> float:
    """Invert SSCD mean similarity into a 0..1 collision-risk score.

    SSCD's mean cosine is 1.0 for identical content and trends to ~0
    for unrelated material. Calibration's invariant is "lower number
    = safer" (driven below ``target.max_self_match``), so we map
    similarity → ``1 - similarity``. A converged SSCD calibration with
    ``--target 0.2`` therefore requires mean SSCD similarity ≤ 0.8
    between source and re-encode — consistent with the
    ``sscd_band()`` "caution" boundary at 0.85.
    """
    # Lazy import — torch is in the optional ``[ml]`` extra.
    from yt_uniquifier.core.qa.sscd import compute_sscd

    result = compute_sscd(source, candidate, cancel_token=cancel)
    return max(0.0, min(1.0, 1.0 - result.mean_similarity))


# Avoid mypy false-positive on the unused-import that we explicitly need.
_ = (Plan,)
