"""Lightweight reverberation via `aecho` for spectral fingerprint shift.

Adding even a small amount of room ambience convolves a multitude of
delayed copies into the signal — that completely changes the chromaprint
sub-fingerprints while leaving speech intelligible.

OPT-IN ONLY. Not in cid_aware (default profile). On dialogue-heavy
content even subtle reverb is audible, so user must explicitly add it
via cid_aggressive or a custom profile.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)

ReverbStyle = Literal["small_room", "medium_room", "hall", "plate"]


class ReverbParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 0 = effectively disabled; 0.15 = subtle "lift"; 0.30 = noticeable room.
    intensity: float = Field(default=0.15, ge=0.0, le=0.5)
    style: ReverbStyle = "small_room"


# (in_gain, out_gain, delays_ms, base_decays-at-intensity-0.15)
_AECHO_PRESETS: dict[ReverbStyle, tuple[float, float, str, list[float]]] = {
    "small_room":  (0.8, 0.88, "40|60",          [0.40, 0.30]),
    "medium_room": (0.8, 0.88, "60|100|180",     [0.50, 0.40, 0.30]),
    "hall":        (0.7, 0.85, "100|200|400|800", [0.50, 0.40, 0.30, 0.20]),
    "plate":       (0.9, 0.92, "20|40|80",       [0.50, 0.30, 0.20]),
}

_BASE_INTENSITY = 0.15


def _build_reverb(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    params = ensure_params(params, ReverbParams)
    in_g, out_g, delays, base_decays = _AECHO_PRESETS[params.style]
    scale = params.intensity / _BASE_INTENSITY if _BASE_INTENSITY else 0.0
    scaled = "|".join(f"{d * scale:.3f}" for d in base_decays)
    out = alloc.next("a")
    filt = f"aecho={in_g}:{out_g}:{delays}:{scaled}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.reverb",
        kind="audio",
        schema=ReverbParams,
        build=_build_reverb,
        defaults={"intensity": 0.15, "style": "small_room"},
    )
)
