"""Sample-rate roundtrip — blurs spectral peaks chromaprint locks onto.

`aresample` is lossy at off-target rates (uses high-quality SoX-style filter).
A two-step trip SR → intermediate → SR introduces small spectral smearing
imperceptible to listeners but visible in chromaprint fingerprinting.

Default delta is 1 Hz (47999 → 48000) which is enough to perturb the FFT
bin boundaries chromaprint hashes against.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class AudioResampleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intermediate_sr: int = Field(default=47999, ge=8000, le=192000)
    target_sr: int = Field(default=48000, ge=8000, le=192000)


def _build_audio_resample(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, AudioResampleParams)
    out = alloc.next("a")
    filt = f"aresample={params.intermediate_sr},aresample={params.target_sr}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.resample",
        kind="audio",
        schema=AudioResampleParams,
        build=_build_audio_resample,
        defaults={"intermediate_sr": 47999, "target_sr": 48000},
    )
)
