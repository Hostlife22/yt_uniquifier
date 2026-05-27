"""Temporal frame perturbation — blackout + drop on a periodic-with-random-phase grid.

Per Fojcik & Syga, "Counteracting Temporal Attacks in Video Copy Detection"
(arXiv:2501.11171, 2025), random blackouts on the order of 1/10 frames
reduced neural video-copy-detector mean Average Precision (μAP) by 60 %+
on Meta's VSC2022 benchmark. Per-frame *drop* has a similar effect on
detectors that sample at a fixed stride.

We pick a deterministic-with-random-phase scheme over ffmpeg's
expression-level `random()`:

  - blackout: every K-th frame becomes mid-gray (or pure black with
    `blackout_blur=false`), where K = round(1 / blackout_prob). The phase
    offset (which frame within each K-window gets blacked out) is drawn
    once at build time from the per-run `rng`. Same run → same phase;
    different runs → different phase.
  - drop: every M-th frame is dropped via `select`, M = round(1 / drop_prob),
    phase also drawn from rng.

This avoids ffmpeg-version-dependent `random()` semantics inside expressions
and produces reproducible filter graphs that the unit tests can lock onto.

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


class TemporalJitterParams(BaseModel):
    # Probability per frame of being blacked out. Defaults are conservative —
    # at 24 fps, 0.033 ≈ blackout every 30 frames ≈ once every 1.25 seconds.
    # Fojcik's reference attack used 1/10; that's visible, so we stay lower.
    blackout_prob: float = Field(default=0.033, ge=0.0, le=0.2)
    # Probability per frame of being dropped (one fewer output frame).
    drop_prob: float = Field(default=0.025, ge=0.0, le=0.2)
    # If true, blackout frames become mid-gray (128,128,128) — perceived
    # as a "soft flash". If false, pure black (0,128,128) — sharper.
    blackout_blur: bool = True


def _build_temporal_jitter(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, TemporalJitterParams)
    use_rng = rng if isinstance(rng, _random.Random) else _random.Random()

    parts: list[str] = []

    if params.blackout_prob > 0:
        period = max(2, int(round(1.0 / params.blackout_prob)))
        offset = use_rng.randint(0, period - 1)
        y_const = 128 if params.blackout_blur else 0
        # geq supports the `N` frame-number constant (lutyuv does not).
        # Commas inside the if/eq/mod expression must be escaped with `\,`
        # so they're not read as filter-separator commas at the filter_complex layer.
        cond = f"eq(mod(N\\,{period})\\,{offset})"
        parts.append(
            "geq="
            f"lum='if({cond}\\,{y_const}\\,p(X\\,Y))':"
            f"cb='if({cond}\\,128\\,p(X\\,Y))':"
            f"cr='if({cond}\\,128\\,p(X\\,Y))'"
        )

    if params.drop_prob > 0:
        period = max(2, int(round(1.0 / params.drop_prob)))
        offset = use_rng.randint(0, period - 1)
        parts.append(f"select='not(eq(mod(n\\,{period})\\,{offset}))'")

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
