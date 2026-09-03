"""Quality metric selection without cross-metric score substitution.

VMAF is used whenever it produces a score, including a very low score.  SSIM
is used only when VMAF is unavailable.  pHash is deliberately excluded: visual
fingerprint similarity is not a perceptual quality metric and cannot safely
replace a failed VMAF/SSIM measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.qa import ssim, vmaf

QualityMetric = Literal["vmaf", "ssim"]


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

    1. VMAF whenever it returns a score, even if the result is very low.
    2. SSIM × 100 only if VMAF is unavailable.

    Raises when neither comparable quality metric is available.  Returning a
    pHash-derived number here previously converted infrastructure/registration
    failures into an apparently valid quality score.
    """
    v = vmaf.compute(
        input_path, output_path,
        threads=threads, subsample=subsample, hdr_aware=hdr_aware,
    )
    if v.score is not None:
        if not math.isfinite(v.score) or not 0.0 <= v.score <= 100.0:
            raise PipelineError(f"invalid VMAF score: {v.score!r}")
        return QualityScore(value=v.score, metric="vmaf", raw=v.score)

    vmaf_note = f"VMAF skipped: {v.note}" if v.note else "VMAF unavailable"

    s = ssim.compute(input_path, output_path)
    if s.score is not None:
        if not math.isfinite(s.score) or not 0.0 <= s.score <= 1.0:
            raise PipelineError(f"invalid SSIM score: {s.score!r}")
        return QualityScore(
            value=s.score * 100.0, metric="ssim", raw=s.score,
            note=vmaf_note,
        )

    ssim_note = f"SSIM skipped: {s.note}" if s.note else "SSIM unavailable"
    raise PipelineError(
        "no comparable quality metric is available: "
        f"{vmaf_note}; {ssim_note}"
    )
