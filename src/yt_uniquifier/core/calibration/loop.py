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

from yt_uniquifier.core.calibration.intensity import scale_profile
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan, Profile, Segment
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.qa import vmaf as vmaf_mod
from yt_uniquifier.core.qa.cid_predict import predict
from yt_uniquifier.core.segmenter import stream_copy_extract


@dataclass(frozen=True)
class CalibrationTarget:
    max_self_match: float = 0.2
    min_vmaf: float = 88.0
    max_iterations: int = 5
    test_clip_sec: float = 60.0


@dataclass(frozen=True)
class CalibrationStep:
    iteration: int
    intensity_factor: float
    profile: Profile
    self_match: float
    vmaf: float | None
    duration_sec: float
    note: str | None = None


@dataclass(frozen=True)
class CalibratedResult:
    profile: Profile
    factor: float
    steps: list[CalibrationStep]
    converged: bool
    final_self_match: float
    final_vmaf: float | None
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
) -> CalibratedResult:
    """Bisect intensity_factor on a short clip until target is met."""
    work_dir.mkdir(parents=True, exist_ok=True)
    clip = _cut_test_clip(input_path, work_dir, target.test_clip_sec)

    steps: list[CalibrationStep] = []
    factor = 1.0
    best: CalibrationStep | None = None
    converged = False

    for i in range(1, target.max_iterations + 1):
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
            ))
            cid = predict(clip, out_path)
            vmaf = vmaf_mod.compute(clip, out_path)
            vmaf_score = vmaf.score
        except Exception as exc:
            step = _step(i, factor, scaled, cid_self=1.0,
                         vmaf=None, note=f"iteration failed: {exc}")
            steps.append(step)
            if on_step:
                on_step(step)
            factor *= 1.5  # try harder
            continue

        step = _step(i, factor, scaled,
                     cid_self=cid.match_probability_self,
                     vmaf=vmaf_score, note=None)
        steps.append(step)
        if on_step:
            on_step(step)
        best = _better(best, step, target)

        if cid.match_probability_self <= target.max_self_match and \
                (vmaf_score is None or vmaf_score >= target.min_vmaf):
            converged = True
            break

        if cid.match_probability_self > target.max_self_match:
            factor *= 1.5
        elif vmaf_score is not None and vmaf_score < target.min_vmaf:
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
        final_vmaf=best.vmaf,
        note=None if converged
            else "did not converge within max_iterations; returning best-so-far",
    )


def _step(i: int, factor: float, profile: Profile, *,
          cid_self: float, vmaf: float | None, note: str | None) -> CalibrationStep:
    return CalibrationStep(
        iteration=i, intensity_factor=factor, profile=profile,
        self_match=cid_self, vmaf=vmaf, duration_sec=0.0, note=note,
    )


def _better(
    current: CalibrationStep | None, candidate: CalibrationStep,
    target: CalibrationTarget,
) -> CalibrationStep:
    """Keep the step closest to (self_match <= target, vmaf >= min)."""
    if current is None:
        return candidate
    # Prefer the one that respects both constraints.
    c_ok = candidate.self_match <= target.max_self_match and \
        (candidate.vmaf is None or candidate.vmaf >= target.min_vmaf)
    cur_ok = current.self_match <= target.max_self_match and \
        (current.vmaf is None or current.vmaf >= target.min_vmaf)
    if c_ok and not cur_ok:
        return candidate
    if cur_ok and not c_ok:
        return current
    # Both satisfy or both don't — pick lower self_match; tiebreak on higher VMAF.
    if candidate.self_match < current.self_match:
        return candidate
    if candidate.self_match == current.self_match and \
            (candidate.vmaf or 0) > (current.vmaf or 0):
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


# Avoid mypy false-positive on the unused-import that we explicitly need.
_ = (Plan,)
