"""Experimental timestamp-based blackout/drop perturbation.

The transform is retained for controlled processing of owned or licensed media and
is deliberately excluded from quality-first defaults.  It is a destructive temporal
effect: QA must report its visual/timeline impact independently of similarity
diagnostics.

Older versions sampled absolute frame numbers in a nominal 1440-frame window.  That
made the alleged 60-second pattern last 60 seconds only at 24 FPS, and made CFR/VFR
sources receive different temporal behaviour.  The implementation now samples a
fixed 24 Hz grid expressed in presentation timestamps.  The 60-second pattern is
therefore duration-based for 24/30/60 FPS and VFR sources.
"""

from __future__ import annotations

import math
import random as _random

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)

# A 24 Hz timestamp grid preserves the historical density while making the window
# duration independent of source frame rate. ``WINDOW_FRAMES`` remains as an
# internal compatibility alias for older tests/plugins that imported the constant.
WINDOW_SECONDS = 60
TIME_BUCKETS_PER_SECOND = 24
WINDOW_BUCKETS = WINDOW_SECONDS * TIME_BUCKETS_PER_SECOND
WINDOW_FRAMES = WINDOW_BUCKETS


def _bucket_count(probability: float) -> int:
    if probability <= 0:
        return 0
    return max(1, int(round(WINDOW_BUCKETS * probability)))


def _coprime_stride(rng: _random.Random) -> int:
    """Draw a stride that permutes every timestamp bucket exactly once."""
    while True:
        stride = rng.randrange(1, WINDOW_BUCKETS)
        if math.gcd(stride, WINDOW_BUCKETS) == 1:
            return stride


def _bucket_rank(variable: str, stride: int, offset: int) -> str:
    """Return a bounded-size deterministic permutation expression."""
    bucket = (
        f"floor(mod({variable}\\,{WINDOW_SECONDS})*{TIME_BUCKETS_PER_SECOND})"
    )
    return f"mod({bucket}*{stride}+{offset}\\,{WINDOW_BUCKETS})"


class TemporalJitterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Fraction of 24 Hz timeline buckets blacked out in each 60-second window.
    blackout_prob: float = Field(default=0.033, ge=0.0, le=0.2)
    # Fraction of timeline buckets whose decoded frames are dropped.
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
    params = ensure_params(params, TemporalJitterParams)
    # Prefer the pipeline-supplied rng (derived from plan_hash + segment
    # idx + run_seed). Fall back to a *deterministic* seed so resumed
    # runs reproduce the same indices.
    use_rng = (
        rng if isinstance(rng, _random.Random)
        else _random.Random(params.rng_seed)
    )

    parts: list[str] = []
    blackout_count = _bucket_count(params.blackout_prob)
    drop_count = _bucket_count(params.drop_prob)
    stride = _coprime_stride(use_rng)
    offset = use_rng.randrange(WINDOW_BUCKETS)

    if blackout_count:
        blackout_rank = _bucket_rank("T", stride, offset)
        blackout_condition = f"lt({blackout_rank}\\,{blackout_count})"
        y_const = 128 if params.blackout_blur else 0
        parts.append(
            "geq="
            f"lum='if({blackout_condition}\\,{y_const}\\,p(X\\,Y))':"
            f"cb='if({blackout_condition}\\,128\\,p(X\\,Y))':"
            f"cr='if({blackout_condition}\\,128\\,p(X\\,Y))'"
        )

    if drop_count:
        drop_rank = _bucket_rank("t", stride, offset)
        first_drop_rank = blackout_count
        last_drop_rank = first_drop_rank + drop_count - 1
        drop_condition = (
            f"between({drop_rank}\\,{first_drop_rank}\\,{last_drop_rank})"
        )
        parts.append(f"select='not({drop_condition})'")

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
