"""Haas effect — delay one stereo channel by a few milliseconds.

Smitelli (2010) showed that full stereo phase inversion breaks YouTube
CID audio fingerprinting. Phase inversion sounds unnatural. The Haas
("precedence") effect is its mono-compatible cousin: a 5–30 ms delay on
one channel is perceived as a single fused source, slightly wider in the
stereo image. It shifts the cross-channel phase relationship that audio
fingerprinters cross-correlate, without the audible artefact of inversion.

Filter: `adelay=0|N` — left channel unchanged (0 ms), right delayed by N ms.
ffmpeg `adelay` since 3.x accepts the per-channel pipe-separated form.

Source: Scott Smitelli, "Fun with YouTube's Audio Content ID System" (2010).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class HaasStereoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Right-channel delay in milliseconds. Within 5–30 ms the perceptual
    # system fuses the two channels as one source ("precedence effect");
    # above ~40 ms it starts to sound like a discrete echo.
    delay_ms: float = Field(default=15.0, ge=1.0, le=40.0)
    # Per-run jitter ±N ms around delay_ms (requires rng).
    randomize_within_ms: float = Field(default=0.0, ge=0.0, le=10.0)


def _build_haas(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, HaasStereoParams)
    delay = params.delay_ms
    if params.randomize_within_ms > 0 and rng is not None:
        from random import Random as _Random
        assert isinstance(rng, _Random)
        delay += rng.uniform(-params.randomize_within_ms, params.randomize_within_ms)
        delay = max(1.0, min(40.0, delay))
    delay_int = int(round(delay))
    out = alloc.next("a")
    filt = f"adelay=0|{delay_int}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.haas_stereo",
        kind="audio",
        schema=HaasStereoParams,
        build=_build_haas,
        defaults={"delay_ms": 15.0, "randomize_within_ms": 0.0},
    )
)
