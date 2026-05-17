"""Independent pitch and tempo via `asetrate + aresample + atempo`.

Math:
- asetrate=SR*pitch shifts pitch but also speed
- aresample=SR restores sample rate
- atempo compensates so the final tempo is `tempo`

So target_atempo = tempo / pitch. We cascade atempo nodes when the target
falls outside `atempo`'s per-instance range to maximize compatibility with
older ffmpeg builds.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0  # conservative; modern ffmpeg supports 0.5..100 but old builds cap at 2.0


class PitchTempoParams(BaseModel):
    pitch: float = Field(default=1.005, ge=0.5, le=2.0)
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int = Field(default=48000, ge=8000, le=192000)


def cascade_atempo(target: float) -> str:
    """Return a chain like 'atempo=0.8,atempo=0.625' that combines to `target`.

    Each factor stays within [ATEMPO_MIN, ATEMPO_MAX]. Order does not matter.
    """
    if target <= 0:
        raise ValueError(f"atempo target must be positive, got {target}")
    if ATEMPO_MIN <= target <= ATEMPO_MAX:
        return f"atempo={target:.6f}"

    factors: list[float] = []
    remaining = target
    if target > ATEMPO_MAX:
        while remaining > ATEMPO_MAX:
            factors.append(ATEMPO_MAX)
            remaining /= ATEMPO_MAX
    else:
        while remaining < ATEMPO_MIN:
            factors.append(ATEMPO_MIN)
            remaining /= ATEMPO_MIN
    factors.append(remaining)
    return ",".join(f"atempo={f:.6f}" for f in factors)


def _build_pitch_tempo(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str
) -> FilterChain:
    assert isinstance(params, PitchTempoParams)
    out = alloc.next("a")
    compensate = params.tempo / params.pitch
    atempo_chain = cascade_atempo(compensate)
    filt = (
        f"asetrate={params.sample_rate}*{params.pitch:.6f},"
        f"aresample={params.sample_rate},"
        f"{atempo_chain}"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.pitch_tempo",
        kind="audio",
        schema=PitchTempoParams,
        build=_build_pitch_tempo,
        defaults={"pitch": 1.005, "tempo": 1.0, "sample_rate": 48000},
    )
)
