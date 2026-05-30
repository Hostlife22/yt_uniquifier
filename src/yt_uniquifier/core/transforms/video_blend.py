"""Frame blend with a B-video (port of the legacy AB approach, as a native filter).

Requires an extra `-i` input. Pipeline detects `extra_inputs` and rewrites
references after assigning input indices.

The B-video reference in `filter_str` uses the placeholder token `__B__` for
the input stream; pipeline replaces it with the concrete `[N:v]` label.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

B_INPUT_PLACEHOLDER = "__B__"


class BlendBParams(BaseModel):
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
    assert isinstance(params, BlendBParams)
    out = alloc.next("v")
    scaled = alloc.next("v")
    a_ref = alloc.next("v")
    # Scale B to A's dimensions, then blend with the given opacity.
    a_opacity = 1.0 - params.opacity
    # The pipeline wraps every chain as `[in_label]<filter_str>[out_label]`,
    # so `filter_str` must NOT start with its own `[in_lbl]` token — that
    # would double the prefix (`[in_lbl][in_lbl][__B__]scale2ref…`) and
    # produce an invalid -filter_complex argument. blend_b is the only
    # multi-input transform; the second input arrives via the `__B__`
    # placeholder that the pipeline rewrites to `[N:v]` after assigning
    # an `-i` index.
    filt = (
        f"[{B_INPUT_PLACEHOLDER}]scale2ref=w=iw:h=ih[{scaled}][{a_ref}];"
        f"[{a_ref}][{scaled}]blend=all_expr='A*{a_opacity:.4f}+B*{params.opacity:.4f}'"
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
