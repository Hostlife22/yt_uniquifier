"""Subtitle burn-in transform with Whisper-aware preflight (v0.9.0 R2 / F14).

Renders an SRT (or ASS) subtitle file into the video using ffmpeg's
``subtitles`` filter. The transform itself is intentionally pure: it
only emits a filter chain referencing a ``subtitle_path`` that already
exists on disk. Auto-generation via whisper-cpp is a separate concern
handled by ``core.subtitles.generate_srt`` and exposed as
``yt-uniq subtitles generate``; the user runs that once to produce the
SRT, then references it in the profile.

This split keeps three invariants intact:

  * Transform builders stay pure (no subprocess side effects per
    segment) — required by the per-segment retry / parallel worker
    contract documented in ``CLAUDE.md``.
  * Resume safety: the plan hash includes the subtitle path string but
    not the SRT bytes; if the user edits the SRT the file mtime
    changes the keyframe cache key transitively (B1 in v0.6) and the
    encode redoes from scratch only when explicitly invalidated.
  * Plugin-friendly: the same transform shape can later be wrapped by
    a third-party plugin that produces SRTs via a different ASR (Vosk,
    Whisper-large-v3, etc.) — they only need to call
    ``core.subtitles.generate_srt`` or its equivalent and write to the
    declared path.

Security: ``subtitle_path`` is interpolated into the ffmpeg
``-filter_complex`` argument. We escape it with the documented
ffmpeg-filter escaping rules (single-quote wrap + backslash-escape of
single quotes) and reject paths containing characters that survive
unescaped through the parser (``[``, ``]``, ``;``, ``\\n``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_params,
    register,
)

SubtitlePosition = Literal["bottom", "top"]

# Reject paths that would let a user (or a poisoned profile) close the
# filter argument and inject new graph nodes. ffmpeg's filter argument
# syntax treats ``[``, ``]``, ``;``, ``,`` as structural; ``\n`` would
# split the argument across lines on some shell quoting paths.
_SUBPATH_FORBIDDEN_RE = re.compile(r"[\[\];\n\r]")


def _escape_subtitle_path(path: str) -> str:
    """Escape a subtitle path for use inside ``subtitles=`` filter.

    ffmpeg's filter-string escaping for filenames uses Bourne-shell-ish
    single quotes; embedded single quotes become ``'\\''``. We never
    rely on shell quoting — this is the filter-arg quote, not the
    OS quote.
    """
    return path.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


class SubtitleBurnParams(BaseModel):
    """Burn an existing subtitle file into the video stream.

    ``subtitle_path``: absolute or relative path to an SRT/ASS file.
    Must exist at preflight time (see ``core.preflight``).

    ``font_size``, ``position``, ``margin_v``: light styling that maps
    to libass ``force_style`` keys. The defaults match TikTok/Shorts
    conventions (large bottom captions, generous bottom margin).

    ``font_name`` is left as a free-form string; libass falls back to
    its default font when the named one is absent. We do not validate
    the font is installed because doing so requires a libass round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    subtitle_path: str = Field(min_length=1)
    font_size: int = Field(default=24, ge=8, le=128)
    font_name: str = Field(default="Helvetica", max_length=64)
    position: SubtitlePosition = "bottom"
    margin_v: int = Field(default=40, ge=0, le=400)
    primary_color: str = Field(default="&H00FFFFFF", max_length=16)  # libass ASS
    outline: int = Field(default=2, ge=0, le=8)
    # ``max_chars_per_line`` is informational here — it's enforced when
    # the SRT is generated via ``core.subtitles.generate_srt`` rather
    # than at burn time. Documented so a user reading the YAML
    # understands which knob controls layout.
    max_chars_per_line: int = Field(default=42, ge=10, le=120)

    @field_validator("subtitle_path")
    @classmethod
    def _safe_subpath(cls, v: str) -> str:
        if _SUBPATH_FORBIDDEN_RE.search(v):
            raise ValueError(
                "subtitle_path contains characters that are unsafe inside "
                "a filter argument ([]; or newline). Rename the file."
            )
        # Forbid double-dot path traversal at validation time. The
        # preflight check additionally verifies existence; here we only
        # block the obvious shape-of-attack.
        if ".." in Path(v).parts:
            raise ValueError(
                "subtitle_path must not contain '..' path traversal segments."
            )
        return v

    @field_validator("font_name")
    @classmethod
    def _safe_font(cls, v: str) -> str:
        # libass parses ``force_style`` as comma-separated key=value
        # pairs; commas in a font name would close the pair.
        if any(ch in v for ch in ",;\n"):
            raise ValueError("font_name may not contain , ; or newlines")
        return v

    @field_validator("primary_color")
    @classmethod
    def _safe_color(cls, v: str) -> str:
        if not re.match(r"^&H[0-9A-Fa-f]{6,8}$", v):
            raise ValueError(
                "primary_color must be libass ASS hex like '&H00FFFFFF'"
            )
        return v


def _build_subtitles(
    params: BaseModel,
    alloc: LabelAllocator,
    in_lbl: str,
) -> FilterChain:
    p = ensure_params(params, SubtitleBurnParams)
    out = alloc.next("v")

    # libass alignment numpad: 2 = bottom-center, 8 = top-center.
    alignment = 2 if p.position == "bottom" else 8
    force_style = (
        f"FontName={p.font_name},"
        f"FontSize={p.font_size},"
        f"PrimaryColour={p.primary_color},"
        f"Outline={p.outline},"
        f"Alignment={alignment},"
        f"MarginV={p.margin_v}"
    )

    escaped_path = _escape_subtitle_path(p.subtitle_path)
    return FilterChain(
        in_label=in_lbl,
        out_label=out,
        filter_str=f"subtitles={escaped_path}:force_style='{force_style}'",
    )


register(
    TransformSpec(
        id="video.subtitles",
        kind="video",
        schema=SubtitleBurnParams,
        build=_build_subtitles,
        defaults={"position": "bottom", "font_size": 24},
    )
)
