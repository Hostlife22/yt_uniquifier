"""Independent pitch and tempo via `asetrate + aresample + atempo`.

Math:
- asetrate=SR*pitch shifts pitch but also speed
- aresample=SR restores sample rate
- atempo compensates so the final tempo is `tempo`

The sample clock is integer-valued, so target_atempo = tempo / actual_pitch,
where actual_pitch = round(SR * pitch) / SR. We cascade atempo nodes when the target
falls outside `atempo`'s per-instance range to maximize compatibility with
older ffmpeg builds.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    ensure_rng,
    register,
)

ATEMPO_MIN = 0.5
ATEMPO_MAX = 2.0  # conservative; modern ffmpeg supports 0.5..100 but old builds cap at 2.0

PitchMethod = Literal["asetrate", "rubberband"]


class PitchTempoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # v0.2 default bumped 1.005 → 1.012 (~21 cents); v0.3.1 introduces
    # method='rubberband' which preserves formants and lets cid_aware push
    # pitch to 1.04 without the "chipmunk" effect.
    pitch: float = Field(default=1.012, ge=0.5, le=2.0)
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int = Field(default=48000, ge=8000, le=192000)
    # If > 0, each run jitters the actual pitch by uniform(-rw, +rw).
    randomize_within: float = Field(default=0.0, ge=0.0, le=0.05)
    # 'asetrate' (legacy, default) shifts the sample-rate header — fast but
    # changes formants, so anything above ~2% sounds chipmunk-like.
    # 'rubberband' uses the ffmpeg rubberband filter (phase vocoder) which
    # preserves formants — larger shifts stay natural. Requires ffmpeg
    # built with --enable-librubberband (Homebrew default has it).
    method: PitchMethod = "asetrate"


def cascade_atempo(target: float) -> str:
    """Return a chain like 'atempo=0.8,atempo=0.625' that combines to `target`.

    Each factor stays within [ATEMPO_MIN, ATEMPO_MAX]. Order does not matter.
    """
    if not math.isfinite(target) or target <= 0:
        raise ValueError(f"atempo target must be positive, got {target}")
    if ATEMPO_MIN <= target <= ATEMPO_MAX:
        return f"atempo={_tempo_literal(target)}"

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
    return ",".join(f"atempo={_tempo_literal(f)}" for f in factors)


def _tempo_literal(value: float) -> str:
    short = f"{value:.6f}"
    return short if float(short) == value else f"{value:.12f}"


def _build_pitch_tempo(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    params = ensure_params(params, PitchTempoParams)
    out = alloc.next("a")
    pitch = params.pitch
    if params.randomize_within > 0 and rng is not None:
        rng = ensure_rng(rng)
        pitch = pitch + rng.uniform(-params.randomize_within, params.randomize_within)
        # Stay within sanity bounds.
        pitch = max(0.5, min(2.0, pitch))
    if params.method == "rubberband":
        # rubberband does both pitch+tempo in one pass without sample-rate
        # tricks, so formants are preserved.
        filt = f"rubberband=pitch={pitch:.6f}:tempo={params.tempo:.6f}"
    else:
        # Legacy asetrate path — fast, but distorts formants past ~2% shift.
        # asetrate's output clock is an integer, not SR * the nominal pitch.
        # Compensating nominal pitch accumulated ~86 ms over 3 h at 44.1 kHz
        # / pitch=1.0004, despite an exact pad/trim endpoint contract.
        shifted_rate = round(params.sample_rate * pitch)
        compensate = params.tempo * params.sample_rate / shifted_rate
        filt = (
            f"asetrate={shifted_rate},"
            f"aresample={params.sample_rate}"
        )
        # atempo=1 is not a sample-exact identity (WSOLA may move a transient
        # after a long silent interval). Do not invoke it for a unity clock.
        if compensate != 1.0:
            filt += f",{cascade_atempo(compensate)}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.pitch_tempo",
        kind="audio",
        schema=PitchTempoParams,
        build=_build_pitch_tempo,
        defaults={"pitch": 1.012, "tempo": 1.0, "sample_rate": 48000},
    )
)
