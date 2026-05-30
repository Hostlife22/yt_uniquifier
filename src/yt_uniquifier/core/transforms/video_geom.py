"""Geometric video transforms: micro-crop with rescale, micro-rotate."""

from __future__ import annotations

import random
import re

from pydantic import BaseModel, Field, field_validator

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

# Allow only ffmpeg colour-name forms safe to interpolate into
# `-filter_complex`. A `;`/`[`/`]`/`,` here would close the current
# filter fragment and inject new graph nodes from a shared profile.
# Accepts:  black | white | gray | green | red | 0xRRGGBB | #RRGGBB.
_SAFE_COLOR_RE = re.compile(r"^(?:#|0x)?[A-Za-z0-9]{1,16}$")


class CropResizeParams(BaseModel):
    max_strength: float = Field(default=0.03, ge=0.0, le=0.10)
    rng_seed: int | None = None


def _build_crop_resize(
    params: BaseModel,
    alloc: LabelAllocator,
    in_lbl: str,
    *,
    rng: random.Random | None = None,
) -> FilterChain:
    assert isinstance(params, CropResizeParams)
    # Prefer the plan-level rng (seeded from run_seed / per-segment derived
    # seed) so resumed runs reproduce the same crop. Falling back to a
    # fresh Random(None) would re-roll non-deterministically and violate
    # CLAUDE.md invariant #10 (deterministic per-segment seeds).
    rng = rng or random.Random(params.rng_seed)
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
    # For SDR sources black=0,0,0 is fine. For HDR (PQ/HLG) pure 0 is below
    # legal video range and gets clipped by the encoder; near-black 16,16,16
    # in 10-bit (0x101010 in 8-bit terms) reads as black after PQ EOTF.
    fillcolor_sdr: str = "black"
    fillcolor_pq: str = "0x101010"

    @field_validator("fillcolor_sdr", "fillcolor_pq")
    @classmethod
    def _safe_color(cls, v: str) -> str:
        # ffmpeg -filter_complex parses ; [ ] , as structural delimiters;
        # an unescaped string can close the current fragment and inject
        # new graph nodes. A shared profile with
        # `fillcolor_sdr: "black,scale=1:1[x];[x]"` would otherwise
        # redirect the output chain. Reject anything outside hex / name.
        if not _SAFE_COLOR_RE.match(v):
            raise ValueError(
                f"fillcolor must be alphanumeric/hex (e.g. 'black' or "
                f"'0x101010'); got {v!r}",
            )
        return v


def _build_rotate(params: BaseModel, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    assert isinstance(params, RotateParams)
    out = alloc.next("v")
    # Default to SDR fill; pipeline rewrites filter_str for HDR (see FilterGraph).
    filt = (
        f"rotate={params.degrees}*PI/180:fillcolor={params.fillcolor_sdr},"
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


class MirrorParams(BaseModel):
    """Horizontal flip. Completely destroys pHash similarity.

    WARNING: visible on any content with on-screen text, logos, or
    lateralized framing. Default-disabled in shipped CID profiles; the user
    opts in by adding `video.mirror` explicitly to their profile.
    """


def _build_mirror(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    out = alloc.next("v")
    return FilterChain(in_label=in_lbl, out_label=out, filter_str="hflip")


register(
    TransformSpec(
        id="video.mirror",
        kind="video",
        schema=MirrorParams,
        build=_build_mirror,
        defaults={},
    )
)
