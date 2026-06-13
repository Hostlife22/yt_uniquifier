"""Frame blend with a B-video (port of the legacy AB approach, as a native filter).

Requires an extra `-i` input. Pipeline detects `extra_inputs` and rewrites
references after assigning input indices.

The B-video reference in `filter_str` uses the placeholder token `__B__` for
the input stream; pipeline replaces it with the concrete `[N:v]` label.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)

B_INPUT_PLACEHOLDER = "__B__"
# Pipeline-recognised placeholder for the chain's primary in-label. When
# filter_str contains `[__IN__]`, pipeline replaces it with `[in_label]`
# and skips the default `[in_label]<filter_str>` prefix — required for
# multi-input filters like scale2ref where the primary input must NOT be
# first (scale2ref scales its first input, references the second).
IN_PLACEHOLDER = "__IN__"


class BlendBParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    b_video_path: Path
    opacity: float = Field(default=0.03, ge=0.01, le=0.15)

    @field_validator("b_video_path")
    @classmethod
    def _expanded_path(cls, v: Path) -> Path:
        # Expand `~` so a profile written by a different user (different
        # `HOME`) still finds the file. We deliberately do NOT call
        # `.resolve()` because resolving collapses macOS `/tmp` →
        # `/private/tmp` symlinks and changes the path the test
        # snapshots assert against. Existence is not enforced here —
        # ffmpeg's own "No such file" is acceptable as the user-facing
        # error when the path is wrong.
        return Path(v).expanduser()


def _build_blend_b(
    params: BaseModel,
    alloc: LabelAllocator,
    in_lbl: str,
    *,
    rng: object = None,
) -> FilterChain:
    params = ensure_params(params, BlendBParams)
    out = alloc.next("v")
    scaled = alloc.next("v")
    a_ref = alloc.next("v")
    # Scale B to A's dimensions, then blend with the given opacity.
    a_opacity = 1.0 - params.opacity
    # scale2ref requires `[main][ref]` ordering — `main` gets scaled to
    # `ref`'s dimensions. We want B scaled to A's dims, so B must be
    # first and A must be the ref. The default pipeline wrap puts the
    # primary in-label at the start of the chain, so we use the
    # `__IN__` placeholder to position it ourselves. The previous form
    # `[__B__]scale2ref=...` got wrapped as `[in_lbl][__B__]scale2ref...`
    # which silently scaled A to B's dimensions and then blended an
    # unchanged B at 97% with a scaled A at 3% — the opposite of the
    # intended effect.
    filt = (
        f"[{B_INPUT_PLACEHOLDER}][{IN_PLACEHOLDER}]"
        f"scale2ref=w=iw:h=ih[{scaled}][{a_ref}];"
        f"[{a_ref}][{scaled}]blend=all_expr='A*{a_opacity:.4f}+B*{params.opacity:.4f}'"
        f"[{out}]"
    )
    return FilterChain(
        in_label=in_lbl,
        out_label=out,
        filter_str=filt,
        extra_inputs=(str(params.b_video_path),),
    )


register(
    TransformSpec(
        id="video.blend_b",
        kind="video",
        schema=BlendBParams,
        build=_build_blend_b,
        defaults={"opacity": 0.03},
    )
)
