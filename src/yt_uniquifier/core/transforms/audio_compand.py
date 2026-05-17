"""Soft dynamic-range compression via `compand`, with per-run jitter.

Why this matters for CID: chromaprint-style fingerprinting hashes the
audio *envelope* — peaks and troughs in level over time. A constant
loudness target (`audio.loudnorm` to -14 LUFS) keeps that envelope
identical across our outputs, which is exactly the signal Content ID
locks onto. Mild compression with randomised threshold/ratio per run
flattens the envelope differently each time, breaking the lock without
making the audio obviously squashed.

Filter shape (compand):
  attacks/decays in seconds, then a piecewise-linear transfer function
  `points=-80/-80|<threshold>/<knee_out>|0/-3`.
  - -80/-80: noise floor passes through unchanged
  - knee at threshold with the requested ratio
  - 0 → -3 dB caps the loudest peaks
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class CompandParams(BaseModel):
    attack: float = Field(default=0.05, ge=0.001, le=1.0)
    decay: float = Field(default=0.5, ge=0.01, le=2.0)
    # Threshold in dBFS where compression kicks in (e.g. -20 = signals over
    # -20 dB get compressed). Conservative defaults give a "radio" feel.
    threshold_db: float = Field(default=-20.0, ge=-60.0, le=-3.0)
    ratio: float = Field(default=2.5, ge=1.0, le=20.0)
    # When True (with rng), threshold ±3 dB and ratio ±0.5 jitter per run.
    randomize_within: bool = True


def _build_compand(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, CompandParams)
    threshold = params.threshold_db
    ratio = params.ratio
    if params.randomize_within and rng is not None:
        from random import Random as _Random
        assert isinstance(rng, _Random)
        threshold += rng.uniform(-3.0, 3.0)
        ratio = max(1.0, ratio + rng.uniform(-0.5, 0.5))
        # Re-clamp threshold to schema bounds.
        threshold = max(-60.0, min(-3.0, threshold))
    knee_in = -abs(threshold)
    knee_out = knee_in / ratio
    out = alloc.next("a")
    filt = (
        f"compand=attacks={params.attack}:decays={params.decay}:"
        f"points=-80/-80|{knee_in:.2f}/{knee_out:.2f}|0/-3"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.compand",
        kind="audio",
        schema=CompandParams,
        build=_build_compand,
        defaults={
            "attack": 0.05, "decay": 0.5, "threshold_db": -20.0,
            "ratio": 2.5, "randomize_within": True,
        },
    )
)
