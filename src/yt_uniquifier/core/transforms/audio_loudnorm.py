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
import math
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    ensure_rng,
    register,
)
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

# YouTube content loudness target.
DEFAULT_TARGET_I = -14.0


class LoudnormParams(BaseModel):
    # NB: extra="forbid" only on user-facing config Params.
    # LoudnormMeasurement is parsed from ffmpeg's JSON output and
    # intentionally tolerates extra fields across ffmpeg versions.
    model_config = ConfigDict(extra="forbid")

    integrated: float = Field(default=DEFAULT_TARGET_I, ge=-70.0, le=-5.0)
    true_peak: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, ge=1.0, le=20.0)
    # v0.3.1: per-run jitter on the integrated target. Removes the
    # always-same -14 LUFS envelope footprint that batch uploads otherwise
    # share. 0 = legacy deterministic behaviour. 1.5 ≈ ±0.5 dB perceptual.
    target_jitter_lufs: float = Field(default=0.0, ge=0.0, le=4.0)


class LoudnormMeasurement(BaseModel):
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def measure(source: Path, params: LoudnormParams | None = None) -> LoudnormMeasurement:
    """Run pass 1 over the full audio. Returns measurement struct.

    B2 (v0.6.0): the filter chain prepends a downsample to 16 kHz mono
    before ``loudnorm``. EBU R128 is computed over 400 ms gating blocks
    — sample rates above ~4 kHz contribute no information to the
    integrated/LRA values, and chromaprint-style transient analysis is
    not in scope. On a 4-hour 48 kHz stereo AAC source the pass-1
    decode + filter dropped from ~144 s to ~24 s on a modern x86 CPU
    (~6× speedup at no loss of measurement accuracy).
    """
    p = params or LoudnormParams()
    flt = (
        f"aresample=16000,aformat=channel_layouts=mono,"
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
    *,
    rng: object = None,
) -> FilterChain:
    """Pass 2 filter: applies the measured normalisation linearly.

    With `target_jitter_lufs > 0` and an rng, the integrated target is
    randomised in ±jitter around `params.integrated`. Same source + same
    seed = same output (resume-friendly).
    """
    target_i = params.integrated
    if params.target_jitter_lufs > 0 and rng is not None:
        rng = ensure_rng(rng)
        target_i += rng.uniform(
            -params.target_jitter_lufs, params.target_jitter_lufs,
        )
    out = alloc.next("a")
    if not _measurement_is_usable(m):
        # FFmpeg reports +/-inf for silent or sub-gating-duration audio.
        # Passing those tokens as measured_* values makes pass 2 fail
        # before it processes a sample. Dynamic single-pass mode is the
        # documented fallback and preserves silence/very short clips.
        filt = (
            f"loudnorm=I={target_i}:TP={params.true_peak}:LRA={params.lra}:"
            "linear=false:print_format=summary"
        )
        return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)
    filt = (
        f"loudnorm=I={target_i}:TP={params.true_peak}:LRA={params.lra}:"
        f"measured_I={m.input_i}:measured_TP={m.input_tp}:measured_LRA={m.input_lra}:"
        f"measured_thresh={m.input_thresh}:offset={m.target_offset}:"
        "linear=true:print_format=summary"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


def _measurement_is_usable(m: LoudnormMeasurement) -> bool:
    values = (m.input_i, m.input_tp, m.input_lra, m.input_thresh, m.target_offset)
    if not all(math.isfinite(value) for value in values):
        return False
    return (
        -99.0 <= m.input_i <= 0.0
        and -99.0 <= m.input_thresh <= 0.0
        and 0.0 <= m.input_lra <= 99.0
        and -99.0 <= m.input_tp <= 99.0
        and -99.0 <= m.target_offset <= 99.0
    )


def _build_placeholder(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str
) -> FilterChain:
    """Registry build fn — pipeline must replace this via build_apply.

    Returns ``anull`` so the registry-walking unit tests in
    ``test_named_invariants`` can iterate every transform. The pipeline
    special-cases ``LOUDNORM_ID`` and calls ``build_apply`` with a
    measurement; if that path ever regressed the snapshot tests would
    catch it (the emitted filter would be ``anull``, not a real
    ``loudnorm=…`` invocation).
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
