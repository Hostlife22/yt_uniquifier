"""Mild chorus / aphaser — shifts the spectral signature without colouring sound.

Chromaprint hashes 32-band spectral peaks every ~0.124 s. Even a tiny
modulation (5 ms delay, depth 0.02) smears those peaks enough to break
matching, while staying imperceptible on dialogue and most music.

WARNING: at intensity > 0.05 the chorus artefact becomes audible on
sustained tones (classical music, sine-heavy synths). Default is 0.02 —
safe on speech and most pop / film soundtracks.
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


class SpectralSmearParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intensity: float = Field(default=0.02, ge=0.0, le=0.10)
    delay_ms: float = Field(default=5.0, ge=1.0, le=50.0)
    speed: float = Field(default=0.3, ge=0.05, le=2.0)


def _build_spectral_smear(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    params = ensure_params(params, SpectralSmearParams)
    out = alloc.next("a")
    # chorus in_gain:out_gain:delays:decays:speeds:depths
    filt = (
        f"chorus=0.7:0.9:{params.delay_ms}:{params.intensity:.4f}:"
        f"{params.speed}:{params.intensity:.4f}"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.spectral_smear",
        kind="audio",
        schema=SpectralSmearParams,
        build=_build_spectral_smear,
        defaults={"intensity": 0.02, "delay_ms": 5.0, "speed": 0.3},
    )
)
