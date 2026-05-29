"""Temporal frame perturbation — blackout + drop on rng-sampled frame indices.

Per Fojcik & Syga, "Counteracting Temporal Attacks in Video Copy Detection"
(arXiv:2501.11171, 2025), random blackouts on the order of 1/10 frames
reduced neural video-copy-detector mean Average Precision (μAP) by 60 %+
on Meta's VSC2022 benchmark. Per-frame *drop* has a similar effect on
detectors that sample at a fixed stride.

v0.3.3 implemented this as periodic-with-random-phase
(`mod(N, period) == offset`). v0.4.0 switches to **Poisson-sampled frame
indices over a 60-second window**: we draw N random frame indices (where
N = window_frames × probability) at build time and embed them as
`eq(mod(N, W), i0) + eq(mod(N, W), i1) + …`. No detectable periodicity.

The expression repeats every 60 s of video — much longer than the v0.3.3
30-frame period (48× larger window), so even stride-based subsampling
can't reliably miss the perturbations.

Source: Fojcik & Syga, arXiv:2501.11171 (2025).
"""

from __future__ import annotations

import random as _random

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

# 60 s × 24 fps = 1440 frames. Long enough to defeat stride-based detection
# while keeping the embedded expression tractable (~50 terms at default
# probabilities).
WINDOW_FRAMES = 60 * 24


class TemporalJitterParams(BaseModel):
    # Probability per frame of being blacked out. Defaults are conservative —
    # 0.033 ≈ blackout 48 of 1440 frames per minute.
    # Fojcik's reference attack used 1/10; that's visible, so we stay lower.
    blackout_prob: float = Field(default=0.033, ge=0.0, le=0.2)
    # Probability per frame of being dropped (one fewer output frame).
    drop_prob: float = Field(default=0.025, ge=0.0, le=0.2)
    # If true, blackout frames become mid-gray (128,128,128) — perceived
    # as a "soft flash". If false, pure black (0,128,128) — sharper.
    blackout_blur: bool = True
    # Deterministic seed for the fallback RNG when the pipeline does NOT
    # supply rng. A time-seeded Random() would make resumed runs re-roll
    # the blackout/drop indices, breaking the temporal pattern already
    # encoded into completed segments.
    rng_seed: int | None = None


def _build_temporal_jitter(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, TemporalJitterParams)
    # Prefer the pipeline-supplied rng (derived from plan_hash + segment
    # idx + run_seed). Fall back to a *deterministic* seed so resumed
    # runs reproduce the same indices.
    use_rng = (
        rng if isinstance(rng, _random.Random)
        else _random.Random(params.rng_seed)
    )

    parts: list[str] = []
    blackout_set: set[int] = set()

    if params.blackout_prob > 0:
        n = max(1, int(round(WINDOW_FRAMES * params.blackout_prob)))
        blackout_idx = sorted(use_rng.sample(range(WINDOW_FRAMES), n))
        blackout_set = set(blackout_idx)
        cond_terms = "+".join(
            f"eq(mod(N\\,{WINDOW_FRAMES})\\,{i})" for i in blackout_idx
        )
        y_const = 128 if params.blackout_blur else 0
        parts.append(
            "geq="
            f"lum='if({cond_terms}\\,{y_const}\\,p(X\\,Y))':"
            f"cb='if({cond_terms}\\,128\\,p(X\\,Y))':"
            f"cr='if({cond_terms}\\,128\\,p(X\\,Y))'"
        )

    if params.drop_prob > 0:
        n = max(1, int(round(WINDOW_FRAMES * params.drop_prob)))
        # Don't drop frames that were blacked out (double-flagging is wasteful).
        candidate_pool = sorted(set(range(WINDOW_FRAMES)) - blackout_set)
        drop_idx = sorted(use_rng.sample(candidate_pool, min(n, len(candidate_pool))))
        cond_terms = "+".join(
            f"eq(mod(n\\,{WINDOW_FRAMES})\\,{i})" for i in drop_idx
        )
        parts.append(f"select='not({cond_terms})'")

    out = alloc.next("v")
    if not parts:
        return FilterChain(in_label=in_lbl, out_label=out, filter_str="null")
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=",".join(parts))


register(
    TransformSpec(
        id="video.temporal_jitter",
        kind="video",
        schema=TemporalJitterParams,
        build=_build_temporal_jitter,
        defaults={"blackout_prob": 0.033, "drop_prob": 0.025, "blackout_blur": True},
    )
)
