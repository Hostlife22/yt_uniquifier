"""Geometric video transforms: micro-crop with rescale, micro-rotate."""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class CropResizeParams(BaseModel):
    max_strength: float = Field(default=0.03, ge=0.0, le=0.10)
    rng_seed: int | None = None


def _build_crop_resize(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str
) -> FilterChain:
    assert isinstance(params, CropResizeParams)
    rng = random.Random(params.rng_seed)
    s = params.max_strength
    left, right, top, bottom = (rng.uniform(0, s) for _ in range(4))
    cw = max(1 - left - right, 0.5)
    ch = max(1 - top - bottom, 0.5)
    out = alloc.next("v")
    filt = (
        f"crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{left:.4f}:ih*{top:.4f},"
        f"scale=iw/{cw:.4f}:ih/{ch:.4f}:flags=lanczos"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.crop_resize",
        kind="video",
        schema=CropResizeParams,
        build=_build_crop_resize,
        defaults={"max_strength": 0.03},
    )
)


class RotateParams(BaseModel):
    degrees: float = Field(default=0.15, ge=-2.0, le=2.0)


def _build_rotate(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    assert isinstance(params, RotateParams)
    out = alloc.next("v")
    # rotate by small angle, fill with black, then crop overflow back to original size.
    filt = (
        f"rotate={params.degrees}*PI/180:fillcolor=black,"
        "crop=iw:ih"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.rotate",
        kind="video",
        schema=RotateParams,
        build=_build_rotate,
        defaults={"degrees": 0.15},
    )
)
