"""Video playback rate change via `setpts`.

If used, the matching audio side must apply the same rate via audio.pitch_tempo
or audio.tempo — pipeline does not enforce this; profiles are expected to
declare both consistently.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)


class SpeedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: float = Field(default=1.0, ge=0.5, le=2.0)


def _build_speed(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    params = ensure_params(params, SpeedParams)
    out = alloc.next("v")
    # setpts=PTS/rate produces playback at `rate`x. rate>1 is faster.
    filt = f"setpts=PTS/{params.rate}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.speed",
        kind="video",
        schema=SpeedParams,
        build=_build_speed,
        defaults={"rate": 1.0},
    )
)
