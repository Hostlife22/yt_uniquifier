"""Subtle film-grain via the ffmpeg `noise` filter."""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class NoiseParams(BaseModel):
    strength: int = Field(default=4, ge=0, le=100)


def _build_noise(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    assert isinstance(params, NoiseParams)
    out = alloc.next("v")
    filt = f"noise=alls={params.strength}:allf=t+u"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.noise",
        kind="video",
        schema=NoiseParams,
        build=_build_noise,
        defaults={"strength": 4},
    )
)
