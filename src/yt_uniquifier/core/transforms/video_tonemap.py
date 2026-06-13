"""HDR → SDR tonemap via zscale-linear → tonemap → zscale-bt709.

When this transform is in a profile, the source's HDR transfer (PQ / HLG)
is collapsed into BT.709 SDR. Pipeline detects its presence and skips the
HDR-keep wrap (Phase 6) since we're no longer staying in HDR.

Algorithms:
    hable    — Hable's filmic curve. Best for cinema-like content; preserves
               midtones, soft highlight roll-off. Default.
    reinhard — Classic photographic operator. Simpler curve, slightly flatter.
    mobius   — Like Reinhard but with a smoother knee; good for bright HDR.
    aces     — ACES filmic. Closest to professional cinema grading;
               slightly more saturated than hable.

`peak` is the mastering display peak (nits). Most HDR cinema masters are
graded against 1000 nits; some streamers go to 4000.

`desat` (0..1) shrinks colour gamut highlights to avoid clipping; 0 keeps
fully saturated highlights (good for animation), 0.5 is conservative for
live action.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

TonemapAlgo = Literal["hable", "reinhard", "mobius", "aces"]


class TonemapSDRParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: TonemapAlgo = "hable"
    peak: float = Field(default=1000.0, ge=100.0, le=10000.0)
    desat: float = Field(default=0.0, ge=0.0, le=1.0)


def _build_tonemap_sdr(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, TonemapSDRParams)
    out = alloc.next("v")
    # tonemap's peak= is in linear-light units relative to npl; for an
    # `npl=1000` linearise we pass peak=10 (i.e. 1000/100). Most online
    # examples use this same ratio.
    tm_peak = params.peak / 100.0
    filt = (
        f"zscale=t=linear:npl={params.peak},"
        f"tonemap={params.algorithm}:desat={params.desat:.4f}:peak={tm_peak:.4f},"
        "zscale=t=bt709:m=bt709:p=bt709:r=tv,"
        "format=yuv420p"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.tonemap_sdr",
        kind="video",
        schema=TonemapSDRParams,
        build=_build_tonemap_sdr,
        defaults={"algorithm": "hable", "peak": 1000.0, "desat": 0.0},
    )
)
