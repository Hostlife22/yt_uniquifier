"""Mild audio EQ via chained `equalizer` filters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class AudioEqParams(BaseModel):
    bands: list[tuple[float, float]] = Field(
        default_factory=lambda: [(120.0, -0.6), (4500.0, 0.4)]
    )
    width_q: float = Field(default=1.0, ge=0.1, le=10.0)


def _build_audio_eq(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    assert isinstance(params, AudioEqParams)
    out = alloc.next("a")
    parts = [
        f"equalizer=f={freq}:t=q:w={params.width_q}:g={gain}"
        for freq, gain in params.bands
    ]
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=",".join(parts))


register(
    TransformSpec(
        id="audio.eq",
        kind="audio",
        schema=AudioEqParams,
        build=_build_audio_eq,
        defaults={},
    )
)
