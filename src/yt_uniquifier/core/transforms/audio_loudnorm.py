"""EBU R128 loudness normalisation via ffmpeg's `loudnorm` filter.

Two-pass workflow:
1. measure(source) runs ffmpeg with `loudnorm=...:print_format=json -f null -`
   and parses the JSON tail from stderr to get input_i/input_tp/input_lra/...
2. build_apply(params, measurement) emits the final filter that consumes the
   measured values for a single-pass, linear-mode application.

The first pass is expensive (full-file scan). The pipeline runs it once per
source + target tuple and caches the result.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

# YouTube content loudness target.
DEFAULT_TARGET_I = -14.0


class LoudnormParams(BaseModel):
    integrated: float = Field(default=DEFAULT_TARGET_I, ge=-70.0, le=-5.0)
    true_peak: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, ge=1.0, le=20.0)


class LoudnormMeasurement(BaseModel):
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def measure(source: Path, params: LoudnormParams | None = None) -> LoudnormMeasurement:
    """Run pass 1 over the full audio. Returns measurement struct."""
    p = params or LoudnormParams()
    flt = (
        f"loudnorm=I={p.integrated}:TP={p.true_peak}:LRA={p.lra}:print_format=json"
    )
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-vn",
        "-af",
        flt,
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"loudnorm pass 1 timed out for {source}") from exc

    if proc.returncode != 0:
        raise PipelineError(
            f"loudnorm pass 1 failed for {source}: {proc.stderr.strip()[-500:]}"
        )

    return _parse_measurement(proc.stderr)


def _parse_measurement(stderr: str) -> LoudnormMeasurement:
    """Extract the JSON block emitted by loudnorm at end of stderr."""
    # The JSON object is printed standalone, easiest to find with a regex over braces.
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.DOTALL)
    if not match:
        raise PipelineError(
            "could not locate loudnorm JSON block in ffmpeg stderr; "
            f"stderr tail: {stderr[-500:]}"
        )
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"loudnorm JSON not parseable: {exc}") from exc

    return LoudnormMeasurement(
        input_i=float(raw["input_i"]),
        input_tp=float(raw["input_tp"]),
        input_lra=float(raw["input_lra"]),
        input_thresh=float(raw["input_thresh"]),
        target_offset=float(raw["target_offset"]),
    )


def build_apply(
    params: LoudnormParams,
    m: LoudnormMeasurement,
    alloc: LabelAllocator,
    in_lbl: str,
) -> FilterChain:
    """Pass 2 filter: applies the measured normalisation linearly."""
    out = alloc.next("a")
    filt = (
        f"loudnorm=I={params.integrated}:TP={params.true_peak}:LRA={params.lra}:"
        f"measured_I={m.input_i}:measured_TP={m.input_tp}:measured_LRA={m.input_lra}:"
        f"measured_thresh={m.input_thresh}:offset={m.target_offset}:"
        "linear=true:print_format=summary"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


def _build_placeholder(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str
) -> FilterChain:
    """Registry build fn: pipeline must replace this via build_apply after measurement.

    Falls back to a no-op `anull` so unit tests that snapshot the graph without
    actual measurement still produce something valid; the pipeline replaces this
    with the real loudnorm filter at run time.
    """
    out = alloc.next("a")
    return FilterChain(in_label=in_lbl, out_label=out, filter_str="anull")


register(
    TransformSpec(
        id="audio.loudnorm",
        kind="audio",
        schema=LoudnormParams,
        build=_build_placeholder,
        defaults={
            "integrated": DEFAULT_TARGET_I,
            "true_peak": -1.5,
            "lra": 11.0,
        },
    )
)
