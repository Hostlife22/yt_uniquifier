"""Fit-aspect transform for platform-destination profiles (v0.7 R3 / F3).

Resizes the source to a target aspect ratio (and optional pixel
dimensions) using one of three strategies:

* ``crop`` — scale-to-cover then center-crop. Loses outer pixels but
  fills the frame. Default. Right for talking-heads / centered content.
* ``pad_blur`` — scale-to-fit + blurred-background pad. Instagram /
  TikTok aesthetic where the original frame "floats" on a defocused
  copy of itself. Visually softer than black bars.
* ``pad_black`` — scale-to-fit + solid color pad. Predictable, the
  most file-size-efficient (uniform pad compresses well). Useful for
  archival / sharing-mode where preserving every pixel matters.

The transform produces a single ``[in]→[out]`` chain. ``pad_blur``
splits the stream internally and overlays the focused copy on top
of the blurred copy, but the chain remains a single primary input.
``target_width`` / ``target_height`` override the per-aspect default.

Used by the platform profiles shipped in v0.7 R3:
youtube_4k, youtube_1080p, youtube_shorts, tiktok_vertical,
instagram_reels, instagram_square, linkedin_square.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)
from yt_uniquifier.core.transforms.video_blend import IN_PLACEHOLDER

# Default pixel dimensions per aspect — picked to match the most common
# platform requirements at the time of shipping (see profiles/*.yaml).
# Users can override via ``target_width`` / ``target_height``.
_DEFAULT_DIMS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1":  (1080, 1080),
    "4:5":  (1080, 1350),
    "4:3":  (1440, 1080),
}

FitMode = Literal["crop", "pad_blur", "pad_black"]
TargetAspect = Literal["16:9", "9:16", "1:1", "4:5", "4:3"]

# Same ffmpeg-graph injection guard as video_geom.RotateParams — pad
# color is interpolated into the filter_complex string and an unsafe
# character (`;`, `[`, `]`, `,`) could close the fragment and inject
# new graph nodes from a shared profile.
_SAFE_COLOR_RE = re.compile(r"^(?:#|0x)?[A-Za-z0-9]{1,16}$")


class FitAspectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_aspect: TargetAspect
    mode: FitMode = "crop"
    target_width: int | None = Field(default=None, gt=0, le=7680)
    target_height: int | None = Field(default=None, gt=0, le=7680)
    allow_upscale: bool = False
    blur_sigma: float = Field(default=20.0, gt=0.0, le=80.0)
    pad_color: str = "black"

    @field_validator("pad_color")
    @classmethod
    def _safe_color(cls, v: str) -> str:
        if not _SAFE_COLOR_RE.match(v):
            raise ValueError(
                f"pad_color must be alphanumeric/hex (e.g. 'black' or "
                f"'0x101010'); got {v!r}",
            )
        return v


def _resolve_dims(p: FitAspectParams) -> tuple[int, int]:
    """Return the (width, height) actually used in the filter graph.

    Falls back to the per-aspect default when both overrides are unset.
    When only one override is supplied, the other is computed from the
    target aspect so the result still matches it.
    """
    default_w, default_h = _DEFAULT_DIMS[p.target_aspect]
    if p.target_width is not None and p.target_height is not None:
        return p.target_width, p.target_height
    aw, ah = (int(x) for x in p.target_aspect.split(":"))
    if p.target_width is not None:
        return p.target_width, max(int(round(p.target_width * ah / aw / 2) * 2), 2)
    if p.target_height is not None:
        return max(int(round(p.target_height * aw / ah / 2) * 2), 2), p.target_height
    return default_w, default_h


def _resolve_dims_for_source(
    p: FitAspectParams,
    source_width: int,
    source_height: int,
) -> tuple[int, int]:
    """Resolve an exact even canvas without enlarging source detail.

    Crop chooses the largest target-aspect rectangle inside both the source and
    configured caps. Pad chooses the smallest target-aspect canvas containing the
    source, falling back to the largest capped canvas when downscaling is required.
    A factor of two keeps both dimensions even for every shipped integer aspect.
    """
    target_width, target_height = _resolve_dims(p)
    if p.allow_upscale:
        return target_width, target_height
    if source_width < 2 or source_height < 2:
        raise ValueError("source dimensions must both be at least two pixels")
    aspect_width, aspect_height = (int(value) for value in p.target_aspect.split(":"))
    cap_factor = min(target_width // aspect_width, target_height // aspect_height)
    cap_factor -= cap_factor % 2
    if p.mode == "crop":
        source_factor = min(
            source_width // aspect_width,
            source_height // aspect_height,
        )
        factor = min(cap_factor, source_factor)
        factor -= factor % 2
    else:
        required_factor = max(
            math.ceil(source_width / aspect_width),
            math.ceil(source_height / aspect_height),
        )
        required_factor += required_factor % 2
        factor = min(required_factor, cap_factor)
    if factor < 2:
        raise ValueError(
            f"{source_width}x{source_height} cannot produce a valid even "
            f"{p.target_aspect} canvas within {target_width}x{target_height}"
        )
    return aspect_width * factor, aspect_height * factor


def _build_fit_aspect(
    params: BaseModel,
    alloc: LabelAllocator,
    in_lbl: str,
    *,
    rng: object = None,
) -> FilterChain:
    p = ensure_params(params, FitAspectParams)
    w, h = _resolve_dims(p)
    out = alloc.next("v")

    if p.mode == "crop":
        # scale-to-cover (force_original_aspect_ratio=increase) then
        # center-crop down to the exact target size. Loses outer pixels.
        return FilterChain(
            in_label=in_lbl,
            out_label=out,
            filter_str=(
                f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}"
            ),
        )

    if p.mode == "pad_black":
        # scale-to-fit (force_original_aspect_ratio=decrease) then pad
        # the remaining area with a solid color. Preserves every pixel.
        scale_width = str(w) if p.allow_upscale else f"'min(iw,{w})'"
        scale_height = str(h) if p.allow_upscale else f"'min(ih,{h})'"
        divisibility = "" if p.allow_upscale else ":force_divisible_by=2"
        return FilterChain(
            in_label=in_lbl,
            out_label=out,
            filter_str=(
                f"scale={scale_width}:{scale_height}:"
                f"force_original_aspect_ratio=decrease{divisibility}:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={p.pad_color}"
            ),
        )

    # pad_blur — split the primary stream into bg + fg copies, blur the
    # bg to cover, scale the fg to fit, then overlay fg centered on bg.
    # Uses __IN__ + manual [out] so pipeline._wrap_chain_str skips the
    # default `[in]<filter_str>[out]` wrap (same trick as video_blend).
    bg_in = alloc.next("v")
    fg_in = alloc.next("v")
    bg_out = alloc.next("v")
    fg_out = alloc.next("v")
    fg_width = str(w) if p.allow_upscale else f"'min(iw,{w})'"
    fg_height = str(h) if p.allow_upscale else f"'min(ih,{h})'"
    fg_divisibility = "" if p.allow_upscale else ":force_divisible_by=2"
    filt = (
        f"[{IN_PLACEHOLDER}]split=2[{bg_in}][{fg_in}];"
        f"[{bg_in}]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h},gblur=sigma={p.blur_sigma:.2f}[{bg_out}];"
        f"[{fg_in}]scale={fg_width}:{fg_height}:"
        f"force_original_aspect_ratio=decrease{fg_divisibility}:flags=lanczos[{fg_out}];"
        f"[{bg_out}][{fg_out}]overlay=(W-w)/2:(H-h)/2[{out}]"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.fit_aspect",
        kind="video",
        schema=FitAspectParams,
        build=_build_fit_aspect,
        defaults={"mode": "crop"},
    )
)
