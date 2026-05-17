"""Color jitter: ffmpeg `eq` filter."""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class ColorEqParams(BaseModel):
    brightness: float = Field(default=0.01, ge=-0.2, le=0.2)
    contrast: float = Field(default=1.02, ge=0.5, le=2.0)
    gamma: float = Field(default=0.99, ge=0.5, le=2.0)
    saturation: float = Field(default=1.03, ge=0.0, le=3.0)


def _build_color_eq(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    assert isinstance(params, ColorEqParams)
    out = alloc.next("v")
    filt = (
        f"eq=brightness={params.brightness}:contrast={params.contrast}:"
        f"gamma={params.gamma}:saturation={params.saturation}"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.color_eq",
        kind="video",
        schema=ColorEqParams,
        build=_build_color_eq,
        defaults={"brightness": 0.01, "contrast": 1.02, "gamma": 0.99, "saturation": 1.03},
    )
)
