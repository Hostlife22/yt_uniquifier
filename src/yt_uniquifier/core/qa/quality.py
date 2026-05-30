"""Unified quality metric with graceful fallbacks.

VMAF gives the best perceptual signal when it works — but on heavily
distorted output (anything past mild crop+noise+pitch) libvmaf often
returns 0.0 or fails outright. SSIM is more stable in that regime but
less perceptually-tuned. pHash is the last-resort floor that always
returns *some* number.

`quality_score` picks the strongest sensible metric for the given
(input, output) pair and normalises it to a common 0..100 scale so the
calibration loop can apply a single `min_quality` threshold across
metrics. The chosen metric name is carried in the result so the UI can
warn the user when we've fallen back from VMAF.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.qa import phash, ssim, vmaf

QualityMetric = Literal["vmaf", "ssim", "phash"]

# VMAF below this is treated as "unreliable" — libvmaf gives up on
# severely distorted pairs and emits something near zero. Set above 5.0
# to also catch the libvmaf-on-short-clips flap (HIGH-1, 2026-05-30 v2):
# on 15-second test clips libvmaf has been observed to return 7.0 once
# then 0.81 then 0.01 across calibration iterations, defeating
# convergence. Anything below this floor is treated as a non-signal and
# the caller falls back to SSIM.
_VMAF_TRUST_FLOOR = 10.0


@dataclass(frozen=True)
class QualityScore:
    value: float                     # 0..100 on a unified scale
    metric: QualityMetric            # which metric produced it
    raw: float                       # source value before normalisation
    note: str | None = None          # set when we fell back


def quality_score(
    input_path: Path,
    output_path: Path,
    *,
    threads: int = 4,
    subsample: int = 1,
    hdr_aware: bool = False,
) -> QualityScore:
    """Pick the strongest sensible metric for this pair.

    1. VMAF if available AND > _VMAF_TRUST_FLOOR
    2. SSIM × 100 if VMAF is unavailable / unreliable
    3. pHash similarity × 100 as last resort

    Always returns a finite score in [0, 100].
    """
    v = vmaf.compute(
        input_path, output_path,
        threads=threads, subsample=subsample, hdr_aware=hdr_aware,
    )
    if v.score is not None and v.score > _VMAF_TRUST_FLOOR:
        return QualityScore(value=v.score, metric="vmaf", raw=v.score)

    vmaf_note = None
    if v.score is not None and v.score <= _VMAF_TRUST_FLOOR:
        vmaf_note = (
            f"VMAF returned {v.score:.2f} (unreliable on this pair); "
            "falling back to SSIM."
        )
    elif v.note:
        vmaf_note = f"VMAF skipped: {v.note}"

    s = ssim.compute(input_path, output_path)
    if s.score is not None:
        return QualityScore(
            value=s.score * 100.0, metric="ssim", raw=s.score,
            note=vmaf_note,
        )

    ph = phash.compare(input_path, output_path, n=30)
    note_parts = []
    if vmaf_note:
        note_parts.append(vmaf_note)
    if s.note:
        note_parts.append(f"SSIM skipped: {s.note}")
    note_parts.append("Using pHash similarity × 100 as last-resort metric.")
    return QualityScore(
        value=ph.similarity * 100.0, metric="phash", raw=ph.similarity,
        note=" ".join(note_parts),
    )
