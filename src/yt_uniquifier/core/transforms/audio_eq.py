"""Mild audio EQ via chained `equalizer` filters."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    ensure_rng,
    register,
)


class AudioEqParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bands: list[tuple[float, float]] = Field(
        default_factory=lambda: [(120.0, -0.6), (4500.0, 0.4)]
    )
    width_q: float = Field(default=1.0, ge=0.1, le=10.0)
    # If True, freq is multiplied by uniform(0.95, 1.05) and gain shifted by
    # uniform(-jitter_db, +jitter_db) on each run. Keeps the spectral shaping
    # in the same neighbourhood but moves the chromaprint sub-fingerprint.
    randomize_bands: bool = False
    # v0.4.0: was hard-coded 0.2 dB which sits below chromaprint's ~2 dB
    # robustness floor. cid_aware bumps to 1.5 to actually move the FP.
    jitter_db: float = Field(default=0.2, ge=0.0, le=6.0)


def _build_audio_eq(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    params = ensure_params(params, AudioEqParams)
    out = alloc.next("a")
    bands = list(params.bands)
    if params.randomize_bands and rng is not None:
        rng = ensure_rng(rng)
        jd = params.jitter_db
        bands = [
            (freq * rng.uniform(0.95, 1.05), gain + rng.uniform(-jd, jd))
            for freq, gain in bands
        ]
    parts = [
        f"equalizer=f={freq:.2f}:t=q:w={params.width_q}:g={gain:.2f}"
        for freq, gain in bands
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
