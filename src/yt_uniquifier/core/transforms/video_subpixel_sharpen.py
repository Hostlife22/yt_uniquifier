"""Sub-visible-threshold unsharp masking for neural-FP perturbation.

Modern (post-2020) perceptual hashing systems use CNN/transformer
embeddings of the image. Those embeddings are trained to be robust to
classical pixel-domain transforms (crop, brightness, noise) but are
sensitive to *high-frequency texture changes* in the image.

`unsharp` with very small `amount` (≤ 0.10) modifies every pixel by ~1 LSB
at the high-frequency end. This is below visual perception threshold (per
ITU-R BT.500 contrast sensitivity studies) but above the sub-pixel
statistical noise floor that neural embeddings absorb during training.

Source: Singh et al., "Robust Neural Audio Fingerprinting using Music
Foundation Models", arXiv:2511.05399 (NeurIPS 2025) — sister work for
video neural FP shows the same robustness pattern + the same
attack-surface property in the high-frequency band.

Chroma is intentionally left untouched (ca=0.0): chroma sharpening
produces color fringing on edges.
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


class SubpixelSharpenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # unsharp luma amount. ≤0.1 = sub-visible-threshold; 0.3+ is visibly sharp.
    luma_amount: float = Field(default=0.05, ge=0.0, le=0.3)
    # Kernel size for the local-mean subtraction. 5×5 standard.
    # Must be odd (ffmpeg constraint); clamped to {3, 5, 7, 9, 11}.
    radius: int = Field(default=5, ge=3, le=11)


def _build_subpixel_sharpen(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    params = ensure_params(params, SubpixelSharpenParams)
    r = params.radius
    # ffmpeg unsharp requires odd kernel; round up if user passed even value.
    if r % 2 == 0:
        r += 1
    a = params.luma_amount
    out = alloc.next("v")
    # lx/ly = luma kernel size, la = luma amount, ca=0.0 = chroma untouched.
    filt = f"unsharp=lx={r}:ly={r}:la={a:.4f}:cx={r}:cy={r}:ca=0.0"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.subpixel_sharpen",
        kind="video",
        schema=SubpixelSharpenParams,
        build=_build_subpixel_sharpen,
        defaults={"luma_amount": 0.05, "radius": 5},
    )
)
