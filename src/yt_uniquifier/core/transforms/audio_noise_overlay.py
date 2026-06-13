"""Parametric noise overlay mixed into the audio track.

Smitelli (2010) demonstrated that white-noise overlay ≥ 45 % (by amplitude
ratio) breaks YouTube CID audio fingerprinting. 45 % is destructive — speech
intelligibility suffers. A 20–30 % mix (≈ -12 to -10 dB) shifts the
chromaprint sub-fingerprints significantly while keeping speech understandable.

OPT-IN. Enabled only in `cid_aggressive`. The default profile keeps audio
clean; this transform adds an audible noise floor and is best for content
that already has ambient noise (B-roll, action, music-heavy).

Filter shape (3 chains within one filter_complex fragment):
  [in] anull [main];
  anoisesrc=c=pink:r=48000:amplitude=1 [noise];
  [main][noise] amix=inputs=2:weights=<main_w> <noise_w>:duration=first [out]

`anoisesrc` is a *source* filter (0 inputs, 1 output), so it must start its
own chain. `anull` passes the original input through to give us a labelable
node we can pair with [noise] in the final amix.

Source: Scott Smitelli, "Fun with YouTube's Audio Content ID System" (2010).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

NoiseColor = Literal["white", "pink", "brown"]


class NoiseOverlayParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Noise level in dB relative to original. -10 dB ≈ 30 % amplitude;
    # -6 dB ≈ 50 % (destructive — Smitelli's threshold for breaking CID).
    # Default is conservative — -12 dB ≈ 25 % mix.
    noise_db: float = Field(default=-12.0, ge=-40.0, le=-3.0)
    color: NoiseColor = "pink"
    # Per-run jitter ±N dB around noise_db (requires rng).
    randomize_within_db: float = Field(default=0.0, ge=0.0, le=5.0)


def _build_noise_overlay(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, NoiseOverlayParams)
    noise_db = params.noise_db
    if params.randomize_within_db > 0 and rng is not None:
        from random import Random as _Random
        assert isinstance(rng, _Random)
        noise_db += rng.uniform(-params.randomize_within_db, params.randomize_within_db)
        noise_db = max(-40.0, min(-3.0, noise_db))

    noise_weight = 10 ** (noise_db / 20.0)
    main_weight = 1.0 - noise_weight

    main_lbl = alloc.next("a")
    noise_lbl = alloc.next("a")
    out = alloc.next("a")

    # The leading filter consumes [in_lbl] (wrapped by pipeline) and outputs
    # [main_lbl]. Then a source chain produces [noise_lbl]. Then amix combines
    # the two into the chain output (out is appended by pipeline's wrap).
    filt = (
        f"anull[{main_lbl}];"
        f"anoisesrc=c={params.color}:r=48000:amplitude=1[{noise_lbl}];"
        f"[{main_lbl}][{noise_lbl}]"
        f"amix=inputs=2:weights={main_weight:.4f} {noise_weight:.4f}:duration=first"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.noise_overlay",
        kind="audio",
        schema=NoiseOverlayParams,
        build=_build_noise_overlay,
        defaults={"noise_db": -12.0, "color": "pink", "randomize_within_db": 0.0},
    )
)
