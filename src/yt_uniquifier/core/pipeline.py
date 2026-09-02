"""FilterGraph: build the full ffmpeg command for one Plan.

Linear composition:
- video: [0:v:0] -> T1 -> [v1] -> T2 -> [v2] -> ... -> [vout]
- audio: [0:a:0] -> A1 -> [a1] -> ... -> [aout]

Multi-track audio: only the default/first audio stream goes through the
filter graph; remaining audio streams are mapped through `-c:a:N copy`.
Subtitles (text-based only) and chapters are passthrough.

HDR: if source is HDR and profile.keep_hdr=True and encoder advertises 10-bit
support, we propagate the color characteristics and pick a 10-bit pix_fmt.

audio.loudnorm needs a measurement (one-pass scan) before the graph is built.
The pipeline accepts an optional pre-measured `LoudnormMeasurement` to avoid
re-scanning on resume; otherwise it triggers the scan eagerly.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_uniquifier import __version__
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    TransformConfig,
)
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices
from yt_uniquifier.core.transforms import all_ids, get
from yt_uniquifier.core.transforms.audio_loudnorm import (
    LoudnormMeasurement,
    LoudnormParams,
    build_apply,
    measure,
)
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.hdr_wrap import (
    is_color_transform,
    is_tonemap_active,
    needs_linear_wrap,
    wrap_linear,
)
from yt_uniquifier.core.transforms.video_blend import (
    B_INPUT_PLACEHOLDER,
    IN_PLACEHOLDER,
)
from yt_uniquifier.core.transforms.video_fit_aspect import FitAspectParams, _resolve_dims
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

LOUDNORM_ID = "audio.loudnorm"
BLEND_B_ID = "video.blend_b"
OUTPUT_AUDIO_SAMPLE_RATE = 48_000


def _main_audio_bitrate(plan: Plan) -> str:
    selected = selected_audio_relative_indices(plan.source, plan.profile.audio_tracks)
    channels = plan.source.audio[selected[0]].channels if selected else 2
    if channels <= 1:
        return "128k"
    if channels == 2:
        return "384k"
    if channels <= 6:
        return "512k"
    return f"{channels * 128}k"


def _audio_transform_params(plan: Plan, tc: TransformConfig) -> Any:
    """Resolve an audio transform against source/profile-level context.

    Transform schemas intentionally remain profile-facing and pure.  Two values,
    however, cannot be resolved correctly from the transform YAML alone:

    * the ``asetrate`` pitch path must start from the *input* stream rate or it
      changes duration whenever a 44.1 kHz source is processed by a profile whose
      delivery rate is 48 kHz;
    * ``Profile.target_loudness_lufs`` is the default loudnorm target unless the
      transform explicitly overrides ``integrated``.
    """
    spec = get(tc.id)
    raw = {**spec.defaults, **tc.params}
    if (
        tc.id == "audio.pitch_tempo"
        and plan.source.audio
        and raw.get("method", "asetrate") == "asetrate"
    ):
        selected_audio = selected_audio_relative_indices(
            plan.source, plan.profile.audio_tracks,
        )
        if selected_audio:
            raw["sample_rate"] = plan.source.audio[selected_audio[0]].sample_rate
    if tc.id == LOUDNORM_ID and "integrated" not in tc.params:
        raw["integrated"] = plan.profile.target_loudness_lufs
    return spec.schema.model_validate(raw)


def _resolve_loudnorm_target(
    params: LoudnormParams,
    rng: random.Random,
) -> LoudnormParams:
    """Resolve target jitter once so both loudnorm passes use one target."""
    if params.target_jitter_lufs <= 0:
        return params
    integrated = params.integrated + rng.uniform(
        -params.target_jitter_lufs, params.target_jitter_lufs,
    )
    return params.model_copy(update={
        "integrated": integrated,
        "target_jitter_lufs": 0.0,
    })


def _measure_before_loudnorm(
    plan: Plan,
    audio_transforms: list[TransformConfig],
) -> LoudnormMeasurement:
    """Measure the signal that actually enters the first loudnorm transform."""
    alloc = LabelAllocator()
    rng = random.Random(plan.run_seed)
    selected_audio = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    if not selected_audio:
        raise PipelineError("cannot measure loudness without a selected audio stream")
    label = f"0:a:{selected_audio[0]}"
    chains: list[str] = []
    for tc in audio_transforms:
        if tc.id == LOUDNORM_ID:
            break
        spec = get(tc.id)
        resolved = _audio_transform_params(plan, tc)
        chain = call_build(spec, resolved, alloc, label, rng=rng)
        chains.append(_wrap_chain_str(chain.in_label, chain.filter_str, chain.out_label))
        label = chain.out_label
    params = _resolve_loudnorm_target(
        _loudnorm_params_from(plan.profile, audio_transforms), rng,
    )
    if not chains:
        return measure(plan.source.path, params)
    return measure(
        plan.source.path,
        params,
        pre_filter_complex=";".join(chains),
        pre_output_label=label,
    )


def _video_tail_scale(plan: Plan) -> str:
    """Return the final canvas guard for the plan.

    Platform profiles promise exact pixel dimensions. Later micro-crops
    rescale approximately because FFmpeg rounds crop coordinates to pixel
    boundaries, so the generic even-dimension tail can otherwise leave a
    1920x1078 or 3838x2160 result. Re-assert the configured fit-aspect
    canvas at the tail; non-platform profiles keep the legacy even guard.
    """
    for transform in reversed(plan.profile.transforms):
        if transform.enabled and transform.id == "video.fit_aspect":
            spec = get(transform.id)
            params = FitAspectParams.model_validate({**spec.defaults, **transform.params})
            width, height = _resolve_dims(params)
            return f"scale={width}:{height}"
    return "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _color_output_args(plan: Plan) -> list[str]:
    """Write explicit output color tags matching the declared transform path."""
    if not plan.source.video:
        return []
    color = plan.source.video[0].color
    if is_tonemap_active(plan.profile.transforms):
        return [
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            "-color_range", "tv",
        ]
    result: list[str] = []
    if color.primaries != "unknown":
        result += ["-color_primaries", color.primaries]
    if color.transfer != "unknown":
        result += ["-color_trc", color.transfer]
    if color.space != "unknown":
        result += ["-colorspace", color.space]
    if color.color_range != "unknown":
        result += ["-color_range", color.color_range]
    return result


def _wrap_chain_str(in_label: str, filter_str: str, out_label: str) -> str:
    """Wrap a builder's filter_str with the standard ``[in][filter][out]``.

    Multi-input transforms (currently only ``video.blend_b``) include the
    ``__IN__`` placeholder in their filter_str so they can position the
    primary stream after a secondary input (e.g. ``[B][__IN__]scale2ref…``).
    For those, the wrap only substitutes ``[__IN__]`` and emits the
    filter_str raw — the builder is responsible for its own trailing
    ``[out_label]``. Single-input transforms get the legacy wrap.

    Defensive guard (v1.0.1): a builder that emits its own ``[in_label]``
    prefix would otherwise produce a double-prefixed
    ``[in][in]<expr>[out]`` filter_complex that ffmpeg rejects with an
    obscure "Output pad with label '...' is not connected" error. Reject
    the misbehaving builder eagerly with a clear message.
    """
    if f"[{IN_PLACEHOLDER}]" in filter_str:
        return filter_str.replace(f"[{IN_PLACEHOLDER}]", f"[{in_label}]")
    if filter_str.startswith(f"[{in_label}]"):
        raise PipelineError(
            f"chain builder emitted [{in_label}] prefix; use __IN__ "
            "placeholder or return filter_str without the leading label",
        )
    return f"[{in_label}]{filter_str}[{out_label}]"


def _group_runs(
    items: list[TransformConfig],
    predicate: Callable[[str], bool],
) -> list[tuple[bool, list[TransformConfig]]]:
    """Split items into runs of `predicate(id)` True / False, preserving order.

    Returns a list of (is_predicate_true, items_in_this_run) pairs.
    """
    runs: list[tuple[bool, list[TransformConfig]]] = []
    current_flag: bool | None = None
    current: list[TransformConfig] = []
    for tc in items:
        flag = predicate(tc.id)
        if current_flag is None:
            current_flag = flag
            current = [tc]
        elif flag == current_flag:
            current.append(tc)
        else:
            runs.append((current_flag, current))
            current_flag = flag
            current = [tc]
    if current and current_flag is not None:
        runs.append((current_flag, current))
    return runs


def _wrap_color_run_at(
    run: list[TransformConfig],
    alloc: LabelAllocator,
    plan: Plan,
    in_label: str,
    rng: random.Random | None,
) -> tuple[str, str, list[Path]]:
    """Compose one zscale-linear-wrapped block over ``run`` color transforms.

    Returns ``(new_v_label, chain_str, extra_inputs)``. Each transform's
    builder is called against a scratch allocator so we collect only its
    ``filter_str``; the wrapped block exposes a single allocated output
    label from the real allocator.
    """
    scratch = LabelAllocator()
    inner: list[str] = []
    extras: list[Path] = []
    for tc in run:
        spec = get(tc.id)
        params = spec.schema.model_validate({**spec.defaults, **tc.params})
        scratch_chain = call_build(spec, params, scratch, "scratch_in", rng=rng)
        inner.append(scratch_chain.filter_str)
        extras.extend(Path(p) for p in scratch_chain.extra_inputs)

    color = plan.source.video[0].color
    wrapped = wrap_linear(inner, color)
    out_label = alloc.next("v")
    chain_str = f"[{in_label}]{wrapped}[{out_label}]"
    return out_label, chain_str, extras


def _build_video_chain(
    plan: Plan,
    alloc: LabelAllocator,
    in_label: str,
    rng: random.Random,
) -> tuple[str, list[str], list[Path]]:
    """Compose the video transform chain shared by full-file and per-segment modes.

    Encapsulates: HDR linear-wrap grouping for color transforms, the
    ``video.rotate`` fillcolor swap (SDR ``black`` → PQ ``0x101010``),
    and ``__B__`` placeholder propagation via ``extra_inputs``. Both
    ``FilterGraph.build`` and ``build_video_segment_command`` rely on
    this so a segmented HDR encode produces the same color path as
    the single-file encode.

    Returns ``(final_v_label, chain_strings, extra_inputs)``.
    """
    video_transforms = [
        tc for tc in plan.profile.transforms
        if tc.enabled and get(tc.id).kind == "video"
    ]

    tonemap = is_tonemap_active(plan.profile.transforms)
    hdr_wrap_enabled = (
        plan.profile.keep_hdr
        and not tonemap
        and bool(plan.source.video)
        and needs_linear_wrap(plan.source.video[0].color)
    )

    v_label = in_label
    v_chains: list[str] = []
    extra_inputs: list[Path] = []
    for run_is_color, run in _group_runs(video_transforms, is_color_transform):
        if hdr_wrap_enabled and run_is_color and len(run) >= 1:
            v_label, chain_str, run_extras = _wrap_color_run_at(
                run, alloc, plan, v_label, rng,
            )
            v_chains.append(chain_str)
            extra_inputs.extend(run_extras)
        else:
            for tc in run:
                spec = get(tc.id)
                params = spec.schema.model_validate({**spec.defaults, **tc.params})
                chain = call_build(spec, params, alloc, v_label, rng=rng)
                filter_str = chain.filter_str
                # Swap rotate's SDR black fill for an HDR-safe near-black
                # so PQ encoders don't clip the legal video range.
                if hdr_wrap_enabled and tc.id == "video.rotate":
                    sdr = getattr(params, "fillcolor_sdr", "black")
                    hdr = getattr(params, "fillcolor_pq", "0x101010")
                    filter_str = filter_str.replace(
                        f"fillcolor={sdr}", f"fillcolor={hdr}", 1,
                    )
                v_chains.append(
                    _wrap_chain_str(chain.in_label, filter_str, chain.out_label)
                )
                v_label = chain.out_label
                extra_inputs.extend(Path(p) for p in chain.extra_inputs)
    return v_label, v_chains, extra_inputs


@dataclass(frozen=True)
class BuiltCommand:
    """Concrete ffmpeg invocation for a Plan (one segment or the whole file)."""

    args: list[str] = field(default_factory=list)
    filter_complex: str = ""
    output_video_label: str = ""
    output_audio_label: str | None = None
    passthrough_audio_maps: list[str] = field(default_factory=list)
    passthrough_sub_maps: list[str] = field(default_factory=list)
    extra_inputs: list[Path] = field(default_factory=list)
    loudnorm_measurement: LoudnormMeasurement | None = None


class FilterGraph:
    """Build a BuiltCommand from a Plan."""

    def __init__(
        self,
        plan: Plan,
        output: Path,
        *,
        loudnorm_measurement: LoudnormMeasurement | None = None,
    ) -> None:
        self.plan = plan
        self.output = output
        self.alloc = LabelAllocator()
        self._loudnorm_measurement = loudnorm_measurement

    def build(self) -> BuiltCommand:
        audio_transforms = self._enabled(kind="audio")
        video_rng = random.Random(self.plan.run_seed)
        audio_rng = random.Random(self.plan.run_seed)

        # ---- video chain ----
        # Delegated to the module-level helper so the per-segment path
        # (build_video_segment_command) and full-file path produce
        # identical HDR / color handling.
        v_label, v_chains, extra_inputs = _build_video_chain(
            self.plan, self.alloc, "0:v:0", video_rng,
        )

        # Tail: round dims to even (required by libx264/H.264 profiles) and set pix_fmt.
        pix_fmt = self._target_pix_fmt()
        v_out = self.alloc.next("v")
        v_chains.append(f"[{v_label}]{_video_tail_scale(self.plan)},format={pix_fmt}[{v_out}]")

        # ---- audio chain (main track only) ----
        loudnorm_used = any(tc.id == LOUDNORM_ID for tc in audio_transforms)
        if loudnorm_used and self._loudnorm_measurement is None:
            # Use a distinct local so the in-loop `params: BaseModel` below
            # isn't narrowed to LoudnormParams by mypy's flow analysis.
            self._loudnorm_measurement = _measure_before_loudnorm(
                self.plan, audio_transforms,
            )

        a_chains: list[str] = []
        a_label: str | None = None
        selected_audio = selected_audio_relative_indices(
            self.plan.source, self.plan.profile.audio_tracks,
        )
        if selected_audio:
            # `0:a:0` is the ffmpeg *relative* audio specifier — "first
            # audio stream of input 0", regardless of absolute container
            # index. The dead `or a_in` fallback that used to live here
            # is removed: `a_label` starts truthy and is reassigned to
            # `chain.out_label` (also truthy) on every iteration.
            a_label = f"0:a:{selected_audio[0]}"
            for tc in audio_transforms:
                spec = get(tc.id)
                params = _audio_transform_params(self.plan, tc)
                if tc.id == LOUDNORM_ID:
                    # A2 (v0.5.5): explicit raises in place of `assert` so
                    # PYTHONOPTIMIZE=1 / PyInstaller -O builds still catch
                    # a wrong-schema params or a forgotten measure() pass.
                    if not isinstance(params, LoudnormParams):
                        raise PipelineError(
                            f"loudnorm: expected LoudnormParams, "
                            f"got {type(params).__name__}",
                        )
                    if self._loudnorm_measurement is None:
                        raise PipelineError(
                            "loudnorm: measurement missing — "
                            "pass-1 scan was skipped before build()",
                        )
                    resolved_loudnorm = _resolve_loudnorm_target(params, audio_rng)
                    chain = build_apply(
                        resolved_loudnorm, self._loudnorm_measurement, self.alloc,
                        a_label, rng=audio_rng,
                    )
                else:
                    chain = call_build(
                        spec, params, self.alloc, a_label, rng=audio_rng,
                    )
                a_chains.append(
                    _wrap_chain_str(chain.in_label, chain.filter_str, chain.out_label)
                )
                a_label = chain.out_label

        # ---- assemble filter_complex ----
        # blend_b uses B_INPUT_PLACEHOLDER which must be replaced with concrete input refs.
        # Contract: each entry in `extra_inputs` corresponds to exactly one
        # `[__B__]` placeholder occurrence in `filter_complex`, in source order.
        # The post-rewrite assertion catches both a builder that emits more
        # placeholders than it declared inputs for AND a future caller that
        # extends `extra_inputs` without producing a matching placeholder.
        all_chains = v_chains + a_chains
        filter_complex = ";".join(all_chains)
        for idx, _path in enumerate(extra_inputs, start=1):
            if f"[{B_INPUT_PLACEHOLDER}]" not in filter_complex:
                raise PipelineError(
                    f"extra_input #{idx} declared but no [{B_INPUT_PLACEHOLDER}] "
                    "placeholder remains in filter graph",
                )
            filter_complex = filter_complex.replace(
                f"[{B_INPUT_PLACEHOLDER}]", f"[{idx}:v]", 1
            )
        if f"[{B_INPUT_PLACEHOLDER}]" in filter_complex:
            raise PipelineError(
                f"unresolved [{B_INPUT_PLACEHOLDER}] placeholder after rewrite — "
                "builder emitted more placeholders than declared extra_inputs",
            )

        # ---- ffmpeg args ----
        args: list[str] = [
            ffmpeg_bin(),
            "-hide_banner",
            "-y",
            "-i",
            str(self.plan.source.path),
        ]
        for p in extra_inputs:
            args += ["-i", str(p)]

        args += ["-filter_complex", filter_complex]
        args += ["-map", f"[{v_out}]"]
        if a_label is not None:
            if a_chains:
                args += ["-map", f"[{a_label}]"]
            else:
                args += ["-map", a_label]

        passthrough_audio_maps = self._build_audio_passthrough()
        args += passthrough_audio_maps

        passthrough_sub_maps = self._build_subtitle_passthrough()
        args += passthrough_sub_maps

        if self.plan.source.chapters:
            args += ["-map_chapters", "0"]
        else:
            args += ["-map_chapters", "-1"]

        args += self._encoder_args()
        args += _color_output_args(self.plan)
        args += self._audio_output_args(has_main=a_label is not None)
        args += self._container_args()
        args += self._metadata_args()
        args += [str(self.output)]

        return BuiltCommand(
            args=args,
            filter_complex=filter_complex,
            output_video_label=v_out,
            output_audio_label=a_label,
            passthrough_audio_maps=passthrough_audio_maps,
            passthrough_sub_maps=passthrough_sub_maps,
            extra_inputs=extra_inputs,
            loudnorm_measurement=self._loudnorm_measurement,
        )

    # ---- helpers ----

    def _enabled(self, kind: str) -> list[TransformConfig]:
        result: list[TransformConfig] = []
        for tc in self.plan.profile.transforms:
            if not tc.enabled:
                continue
            try:
                spec = get(tc.id)
            except KeyError as exc:
                raise PipelineError(
                    f"unknown transform {tc.id!r}; known: {all_ids()}"
                ) from exc
            if spec.kind == kind:
                result.append(tc)
        return result

    def _loudnorm_params(self, audio_tcs: list[TransformConfig]) -> LoudnormParams:
        return _loudnorm_params_from(self.plan.profile, audio_tcs)

    def _target_pix_fmt(self) -> str:
        if not self.plan.source.video:
            return "yuv420p"
        v = self.plan.source.video[0]
        # Tonemap converts to SDR — output is always 8-bit yuv420p regardless
        # of source HDR / keep_hdr flag.
        if is_tonemap_active(self.plan.profile.transforms):
            return "yuv420p"
        if v.color.is_hdr and self.plan.profile.keep_hdr:
            return "yuv420p10le"
        return "yuv420p"

    def _build_audio_passthrough(self) -> list[str]:
        """Maps for additional audio tracks beyond the main one, with -c copy."""
        indices_for_passthrough = selected_audio_relative_indices(
            self.plan.source, self.plan.profile.audio_tracks,
        )[1:]

        args: list[str] = []
        # `src_idx` is the audio-relative index of each passthrough track;
        # `n` is just its slot in
        # the assembled output, 1-based because :a:0 is the processed main.
        # Using `n` for `0:a:{...}?` would silently remap non-contiguous
        # tracks (e.g. tracks [1, 3] would become [1, 2] of the source).
        for n, src_idx in enumerate(indices_for_passthrough, start=1):
            args += ["-map", f"0:a:{src_idx}?"]
            args += [f"-c:a:{n}", "copy"]
        return args

    def _build_subtitle_passthrough(self) -> list[str]:
        if not self.plan.source.subtitle:
            return ["-sn"]
        if self.plan.profile.output_container in {"mp4", "mov"}:
            if any(stream.is_image_based for stream in self.plan.source.subtitle):
                raise PipelineError(
                    "image-based subtitles require MKV output or prior conversion"
                )
            return ["-map", "0:s?", "-c:s", "mov_text"]
        return ["-map", "0:s?", "-c:s", "copy"]

    def _encoder_args(self) -> list[str]:
        # One policy for the legacy full-file graph and segmented graph.
        # Keeping a second hand-written mapping here made the same Plan use
        # `-q:v 50` on VideoToolbox in one path and capped VBR in the other.
        return _encoder_args_for(self.plan)

    def _max_bitrate(self) -> int:
        v = self.plan.source.video[0] if self.plan.source.video else None
        base = v.bit_rate if (v and v.bit_rate) else 8_000_000
        return int(base * 1.25)

    def _audio_output_args(self, *, has_main: bool) -> list[str]:
        if not has_main:
            return []
        return [
            "-c:a:0", "aac", "-b:a:0", _main_audio_bitrate(self.plan),
            "-ar:a:0", str(OUTPUT_AUDIO_SAMPLE_RATE),
        ]

    def _container_args(self) -> list[str]:
        c = self.plan.profile.output_container
        out: list[str] = []
        if c in {"mp4", "mov"}:
            out += ["-movflags", "+faststart"]
        return out

    def _metadata_args(self) -> list[str]:
        # v0.4.0: do NOT write a custom `encoder=…` tag — ffmpeg's muxer
        # writes its own `encoder=Lavf<version>` which is indistinguishable
        # from any other ffmpeg-built output. A custom string would
        # fingerprint the file as tool-generated.
        from yt_uniquifier.core.metadata import build_metadata_args

        return build_metadata_args(self.plan)


def build_video_segment_command(
    plan: Plan,
    segment_input: Path,
    segment_output: Path,
    *,
    crf_override: int | None = None,
) -> BuiltCommand:
    """Build an ffmpeg command that applies video transforms to one segment.

    The segment file is the input (not the original source). Segment outputs
    intentionally contain video only. Audio, subtitles, and chapters are
    mapped once from the original source during final muxing; carrying copied
    AAC through segment files can shift the video timeline due to encoder delay.
    """
    alloc = LabelAllocator()
    rng = random.Random(plan.run_seed)
    # Same shared helper as FilterGraph.build → identical HDR linear-wrap
    # and rotate-fillcolor behaviour. Previously this path hand-rolled a
    # plain for-loop with no HDR awareness, so segmented HDR encodes with
    # color transforms silently produced wrong colors.
    v_label, v_chains, extra_inputs = _build_video_chain(
        plan, alloc, "0:v:0", rng,
    )

    pix_fmt = _segment_pix_fmt(plan)
    v_out = alloc.next("v")
    v_chains.append(
        f"[{v_label}]setpts=PTS-STARTPTS,{_video_tail_scale(plan)},"
        f"format={pix_fmt}[{v_out}]"
    )

    filter_complex = ";".join(v_chains)
    for idx, _ in enumerate(extra_inputs, start=1):
        if f"[{B_INPUT_PLACEHOLDER}]" not in filter_complex:
            raise PipelineError(
                f"extra_input #{idx} declared but no [{B_INPUT_PLACEHOLDER}] "
                "placeholder remains in segment filter graph",
            )
        filter_complex = filter_complex.replace(
            f"[{B_INPUT_PLACEHOLDER}]", f"[{idx}:v]", 1
        )
    if f"[{B_INPUT_PLACEHOLDER}]" in filter_complex:
        raise PipelineError(
            f"unresolved [{B_INPUT_PLACEHOLDER}] placeholder in segment filter graph",
        )

    args: list[str] = [
        ffmpeg_bin(),
        "-hide_banner",
        "-y",
        "-i", str(segment_input),
    ]
    for p in extra_inputs:
        args += ["-i", str(p)]

    args += ["-filter_complex", filter_complex]
    args += ["-map", f"[{v_out}]"]
    args += ["-an", "-sn"]
    args += ["-map_chapters", "-1"]
    args += _encoder_args_for(plan, crf_override=crf_override)
    args += _color_output_args(plan)
    args += ["-map_metadata", "-1"]
    args += [str(segment_output)]

    return BuiltCommand(
        args=args,
        filter_complex=filter_complex,
        output_video_label=v_out,
        output_audio_label=None,
        passthrough_audio_maps=[],
        passthrough_sub_maps=[],
        extra_inputs=extra_inputs,
    )


def build_video_segment_command_fused(
    plan: Plan,
    segment: Segment,
    source: Path,
    segment_output: Path,
    *,
    crf_override: int | None = None,
) -> BuiltCommand:
    """B3 (v0.6.0): fused single-fork variant of build_video_segment_command.

    Reads directly from the source with `-ss/-t` input seek (keyframe-
    aligned) and applies the filter_complex in the same ffmpeg fork.
    Eliminates the intermediate ``seg_NNNN_src.mkv`` stream-copy file
    that the legacy two-fork path produced — saves ~600 MB peak disk
    per 1080p segment and cuts per-segment wall time by 15-25 % on
    HDD I/O-bound runs.

    PTS handling is explicit and independent of source audio delay:
      - ``-ss <start>`` BEFORE ``-i`` for input seek (keyframe-aligned)
      - ``-t <span>`` AFTER ``-i`` to clamp duration
      - ``setpts=PTS-STARTPTS`` anchors decoded video at zero
    CRIT-2 (2026-05-30 test report) — we use ``-t``, not ``-to``,
    because some MP4 sources carry packet PTS extending past
    ``container.duration``.

    Audio, subtitles, and chapters are deliberately omitted. They are mapped
    once from the full source at final mux, preserving stream topology without
    letting negative AAC priming PTS shift every video segment.
    """
    alloc = LabelAllocator()
    rng = random.Random(plan.run_seed)
    v_label, v_chains, extra_inputs = _build_video_chain(
        plan, alloc, "0:v:0", rng,
    )

    pix_fmt = _segment_pix_fmt(plan)
    v_out = alloc.next("v")
    v_chains.append(
        f"[{v_label}]setpts=PTS-STARTPTS,{_video_tail_scale(plan)},"
        f"format={pix_fmt}[{v_out}]"
    )

    filter_complex = ";".join(v_chains)
    for idx, _ in enumerate(extra_inputs, start=1):
        if f"[{B_INPUT_PLACEHOLDER}]" not in filter_complex:
            raise PipelineError(
                f"extra_input #{idx} declared but no [{B_INPUT_PLACEHOLDER}] "
                "placeholder remains in fused segment filter graph",
            )
        filter_complex = filter_complex.replace(
            f"[{B_INPUT_PLACEHOLDER}]", f"[{idx}:v]", 1
        )
    if f"[{B_INPUT_PLACEHOLDER}]" in filter_complex:
        raise PipelineError(
            f"unresolved [{B_INPUT_PLACEHOLDER}] placeholder in fused segment filter graph",
        )

    span = max(0.001, segment.end_sec - segment.start_sec)
    args: list[str] = [
        ffmpeg_bin(),
        "-hide_banner",
        "-y",
        # Input seek before -i: ffmpeg jumps to the nearest preceding
        # keyframe (cheap; keyframe-aligned per plan_segments). The
        "-ss", f"{segment.start_sec:.6f}",
        "-i", str(source),
        "-t", f"{span:.6f}",
    ]
    for p in extra_inputs:
        args += ["-i", str(p)]

    args += ["-filter_complex", filter_complex]
    args += ["-map", f"[{v_out}]"]
    args += ["-an", "-sn"]
    args += ["-map_chapters", "-1"]
    args += _encoder_args_for(plan, crf_override=crf_override)
    args += _color_output_args(plan)
    args += ["-map_metadata", "-1"]
    args += [str(segment_output)]

    return BuiltCommand(
        args=args,
        filter_complex=filter_complex,
        output_video_label=v_out,
        output_audio_label=None,
        passthrough_audio_maps=[],
        passthrough_sub_maps=[],
        extra_inputs=extra_inputs,
    )


def build_main_audio_command(
    plan: Plan,
    audio_output: Path,
    *,
    loudnorm_measurement: LoudnormMeasurement | None = None,
) -> tuple[BuiltCommand, LoudnormMeasurement | None]:
    """Build an ffmpeg command that processes ONLY the main audio dorozhka.

    Runs over the full source (not segmented), so loudnorm and pitch shift
    operate on a continuous signal — no seam artifacts.

    Returns (command, measurement) — the measurement is returned so the
    caller can cache it (state.json) for resume.
    """
    alloc = LabelAllocator()
    rng = random.Random(plan.run_seed)
    audio_transforms = [
        tc for tc in plan.profile.transforms
        if tc.enabled and get(tc.id).kind == "audio"
    ]
    selected_audio = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    if not audio_transforms or not selected_audio:
        # Nothing to process; signal caller to skip.
        return (
            BuiltCommand(args=[], filter_complex="", output_video_label=""),
            loudnorm_measurement,
        )

    measurement = loudnorm_measurement
    needs_loudnorm = any(tc.id == LOUDNORM_ID for tc in audio_transforms)
    if needs_loudnorm and measurement is None:
        measurement = _measure_before_loudnorm(plan, audio_transforms)

    a_label = f"0:a:{selected_audio[0]}"
    a_chains: list[str] = []
    for tc in audio_transforms:
        spec = get(tc.id)
        params = _audio_transform_params(plan, tc)
        if tc.id == LOUDNORM_ID:
            if not isinstance(params, LoudnormParams):
                raise PipelineError(
                    f"loudnorm: expected LoudnormParams, got {type(params).__name__}",
                )
            ln_params = _resolve_loudnorm_target(params, rng)
            if measurement is None:
                raise PipelineError(
                    "loudnorm: measurement missing — pass-1 scan "
                    "was skipped before build_main_audio_command()",
                )
            chain = build_apply(ln_params, measurement, alloc, a_label, rng=rng)
        else:
            chain = call_build(spec, params, alloc, a_label, rng=rng)
        a_chains.append(
            _wrap_chain_str(chain.in_label, chain.filter_str, chain.out_label)
        )
        a_label = chain.out_label

    filter_complex = ";".join(a_chains)
    args: list[str] = [
        ffmpeg_bin(),
        "-hide_banner",
        "-y",
        "-i", str(plan.source.path),
        "-vn",
        "-filter_complex", filter_complex,
        "-map", f"[{a_label}]",
        "-c:a", "aac", "-b:a", _main_audio_bitrate(plan),
        "-ar", str(OUTPUT_AUDIO_SAMPLE_RATE),
        "-map_metadata", "-1",
        str(audio_output),
    ]
    return (
        BuiltCommand(
            args=args,
            filter_complex=filter_complex,
            output_video_label="",
            output_audio_label=a_label,
            loudnorm_measurement=measurement,
        ),
        measurement,
    )


def build_main_audio_command_windowed(
    plan: Plan,
    audio_output: Path,
    *,
    loudnorm_measurement: LoudnormMeasurement | None = None,
) -> tuple[BuiltCommand, LoudnormMeasurement | None]:
    """Per-window audio processing for `seed_strategy='divergent'`.

    Splits audio into ~60 s windows. Each window's stochastic audio
    transforms (rubberband pitch jitter, Haas delay jitter, compand
    threshold jitter, EQ band jitter, noise overlay db jitter) draws
    from its own seeded RNG derived as
    `derive_segment_seed(plan_hash, AUDIO_WINDOW_NS_OFFSET + idx, run_seed)`.

    Adjacent windows crossfade via `acrossfade=d=0.1`. Loudnorm runs
    once globally on the concatenated stream — single measurement, no
    per-window levels.

    For audio shorter than 2 × WINDOW_SEC, transparently falls back to
    `build_main_audio_command` (single pass).

    Returns same shape as build_main_audio_command for caller symmetry.
    """
    from yt_uniquifier.core.audio_windows import (
        AUDIO_WINDOW_NS_OFFSET,
        CROSSFADE_SEC,
        plan_windows,
    )
    from yt_uniquifier.core.seed_resolver import derive_segment_seed

    windows = plan_windows(plan.source.duration_sec)
    if len(windows) <= 1:
        # Short audio — windowing buys nothing; degrade to legacy path.
        return build_main_audio_command(
            plan, audio_output, loudnorm_measurement=loudnorm_measurement,
        )

    audio_transforms_all = [
        tc for tc in plan.profile.transforms
        if tc.enabled and get(tc.id).kind == "audio"
    ]
    audio_transforms = [tc for tc in audio_transforms_all if tc.id != LOUDNORM_ID]
    needs_loudnorm = any(tc.id == LOUDNORM_ID for tc in audio_transforms_all)

    selected_audio = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    if not audio_transforms_all or not selected_audio:
        return (
            BuiltCommand(args=[], filter_complex="", output_video_label=""),
            loudnorm_measurement,
        )

    measurement = loudnorm_measurement

    alloc = LabelAllocator()
    window_chains: list[str] = []
    window_out_labels: list[str] = []
    # Relative audio specifier — same as FilterGraph.build.
    main_audio_specifier = f"0:a:{selected_audio[0]}"

    for w in windows:
        seg_seed = derive_segment_seed(
            plan.plan_hash, AUDIO_WINDOW_NS_OFFSET + w.idx, plan.run_seed,
        )
        win_rng = random.Random(seg_seed)
        # Adjacent logical windows tile the source.  Extend each side by half
        # the crossfade so the physical overlap is exactly CROSSFADE_SEC.
        # Extending both sides by the full value created 0.2 s of input overlap
        # while acrossfade removed only 0.1 s, growing audio by 0.1 s/boundary.
        cut_in = max(0.0, w.start_sec - w.crossfade_in_sec / 2)
        cut_out = min(
            plan.source.duration_sec,
            w.end_sec + w.crossfade_out_sec / 2,
        )

        # Start the per-window chain: atrim the slice, reset PTS to 0.
        trim_out = alloc.next("a")
        chain_parts = [
            f"[{main_audio_specifier}]atrim=start={cut_in:.4f}:end={cut_out:.4f},"
            f"asetpts=PTS-STARTPTS[{trim_out}]"
        ]
        a_label = trim_out
        # Apply each non-loudnorm transform with the per-window rng.
        for tc in audio_transforms:
            spec = get(tc.id)
            params = _audio_transform_params(plan, tc)
            chain = call_build(spec, params, alloc, a_label, rng=win_rng)
            chain_parts.append(
                _wrap_chain_str(chain.in_label, chain.filter_str, chain.out_label)
            )
            a_label = chain.out_label
        window_chains.append(";".join(chain_parts))
        window_out_labels.append(a_label)

    # Crossfade adjacent windows pairwise. Each step takes previous accumulator
    # + next window's tail, produces a new combined label.
    acrossfade_chains: list[str] = []
    accumulator = window_out_labels[0]
    for next_label in window_out_labels[1:]:
        out_label = alloc.next("a")
        acrossfade_chains.append(
            f"[{accumulator}][{next_label}]"
            f"acrossfade=d={CROSSFADE_SEC}:c1=tri:c2=tri[{out_label}]"
        )
        accumulator = out_label

    resolved_window_loudnorm: LoudnormParams | None = None
    if needs_loudnorm:
        ln_seed = derive_segment_seed(
            plan.plan_hash, AUDIO_WINDOW_NS_OFFSET - 1, plan.run_seed,
        )
        resolved_window_loudnorm = _resolve_loudnorm_target(
            _loudnorm_params_from(plan.profile, audio_transforms_all),
            random.Random(ln_seed),
        )
    if resolved_window_loudnorm is not None and measurement is None:
        measurement = measure(
            plan.source.path,
            resolved_window_loudnorm,
            pre_filter_complex=";".join(window_chains + acrossfade_chains),
            pre_output_label=accumulator,
        )

    # Global loudnorm on the concatenated stream.
    if needs_loudnorm:
        if measurement is None:
            raise PipelineError(
                "loudnorm: measurement missing — pass-1 scan was "
                "skipped before build_main_audio_command_windowed()",
            )
        if resolved_window_loudnorm is None:
            raise PipelineError("loudnorm parameters were not resolved")
        ln_chain = build_apply(
            resolved_window_loudnorm, measurement, alloc, accumulator,
        )
        final_label = ln_chain.out_label
        loudnorm_str = _wrap_chain_str(
            ln_chain.in_label, ln_chain.filter_str, ln_chain.out_label,
        )
        all_chains = window_chains + acrossfade_chains + [loudnorm_str]
    else:
        final_label = accumulator
        all_chains = window_chains + acrossfade_chains

    filter_complex = ";".join(all_chains)
    args: list[str] = [
        ffmpeg_bin(),
        "-hide_banner",
        "-y",
        "-i", str(plan.source.path),
        "-vn",
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-c:a", "aac", "-b:a", _main_audio_bitrate(plan),
        "-ar", str(OUTPUT_AUDIO_SAMPLE_RATE),
        "-map_metadata", "-1",
        str(audio_output),
    ]
    return (
        BuiltCommand(
            args=args,
            filter_complex=filter_complex,
            output_video_label="",
            output_audio_label=final_label,
            loudnorm_measurement=measurement,
        ),
        measurement,
    )


def _segment_pix_fmt(plan: Plan) -> str:
    if not plan.source.video:
        return "yuv420p"
    v = plan.source.video[0]
    if is_tonemap_active(plan.profile.transforms):
        return "yuv420p"
    if v.color.is_hdr and plan.profile.keep_hdr:
        return "yuv420p10le"
    return "yuv420p"


_DEFAULT_X26X_CRF = 18
_DEFAULT_GPU_QUALITY = 19  # nvenc cq, qsv global_quality, amf qp_i/qp_p
# v1.2.0 Task 22 — AV1 CRF defaults.  libaom-av1 and libsvtav1 both use a
# 0..63 CRF scale; CRF 30 ≈ libx264 CRF 18 quality (~VMAF 95 on most
# content).  Hardware AV1 (av1_nvenc / av1_qsv / av1_amf / av1_videotoolbox)
# reuses the existing 0..51 GPU quality scale because their command-line
# knobs (cq / global_quality / qp_i / b:v) are the same family.
_DEFAULT_AV1_CRF = 30


def _encoder_args_for(plan: Plan, *, crf_override: int | None = None) -> list[str]:
    """Same encoder args as FilterGraph._encoder_args, but free function.

    v0.8.0 R5 — accepts an optional ``crf_override`` used by the
    target-VMAF feedback loop in ``segmenter.process_video_segment``.
    For libx264/libx265 the value is the literal CRF. For hardware
    encoders the same delta is applied to their quality knob
    (nvenc cq, qsv global_quality, amf qp_i/qp_p) so the loop can
    drive quality across the whole encoder matrix from one parameter.
    The fallback to the historical defaults
    (CRF 18, GPU quality 19) is preserved when ``crf_override`` is
    ``None`` — no behavioural change for callers that don't opt in.

    v1.2.0 Task 22 — when ``plan.encoder.codec == "av1"``, ``crf_override``
    is interpreted on the AV1 0..63 scale (default 30) for the software
    encoders libaom-av1/libsvtav1, and the historical hardware mapping
    is reused for av1_nvenc/av1_qsv/av1_amf/av1_videotoolbox so a single
    ``crf_override`` drives quality consistently across the whole AV1
    encoder matrix.
    """
    enc = plan.encoder
    name = enc.name
    mb = _max_bitrate_for(plan)
    is_av1 = enc.codec == "av1"
    default_crf = _DEFAULT_AV1_CRF if is_av1 else _DEFAULT_X26X_CRF
    crf_max = 63 if is_av1 else 51
    crf = default_crf if crf_override is None else max(0, min(crf_max, crf_override))
    # Hardware encoders use a parallel 0..51 quality scale; map by
    # preserving the (default_crf - override) delta so a profile that
    # asks for a 2-step reduction yields the same perceptual bump on
    # CPU and GPU paths.
    gpu_q = _DEFAULT_GPU_QUALITY + (crf - default_crf)
    gpu_q = max(0, min(51, gpu_q))
    gpu_q_s = str(gpu_q)
    if enc.vendor == "nvenc":
        return [
            "-c:v", name, "-preset", "p6", "-rc", "vbr", "-cq", gpu_q_s,
            "-b:v", "0", "-maxrate", str(mb), "-bufsize", str(mb * 2),
        ]
    if enc.vendor == "qsv":
        return ["-c:v", name, "-global_quality", gpu_q_s, "-look_ahead", "1"]
    if enc.vendor == "amf":
        return ["-c:v", name, "-rc", "cqp", "-qp_i", gpu_q_s, "-qp_p", gpu_q_s]
    if enc.vendor == "videotoolbox":
        return [
            "-c:v", name, "-b:v", str(mb),
            "-maxrate", str(int(mb * 1.5)), "-bufsize", str(mb * 2),
            # A capability probe must not pass by silently falling back to
            # Apple's software encoder.  The same command is used for the
            # real encode, so a selected VideoToolbox encoder is guaranteed
            # to be backed by hardware for this job's resolution/pixel format.
            "-allow_sw", "0",
        ]
    if enc.vendor == "svtav1":
        # SVT-AV1 preset 0=slowest/best, 13=fastest. preset 8 is the
        # documented "balanced" point and the libsvtav1 ffmpeg default.
        return [
            "-c:v", name, "-preset", "8", "-crf", str(crf),
            "-b:v", "0", "-maxrate", str(mb), "-bufsize", str(mb * 2),
        ]
    if enc.vendor == "libaom":
        # libaom-av1 is CPU-bound (~10× slower than libx264) — cpu-used 4
        # is the documented "good" preset that trades ~5% quality for
        # tractable wall-clock at 1080p.  row-mt + tiles spread one
        # encode across cores when the orchestrator runs only one
        # segment at a time (still useful — the segment-level
        # parallelism is bounded by max_parallel which is cpu_count/2).
        return [
            "-c:v", name, "-cpu-used", "4", "-row-mt", "1",
            "-tile-columns", "2", "-tile-rows", "1",
            "-crf", str(crf), "-b:v", "0",
            "-maxrate", str(mb), "-bufsize", str(mb * 2),
        ]
    result = [
        "-c:v", name, "-preset", "medium", "-crf", str(crf),
        "-maxrate", str(mb), "-bufsize", str(mb * 2),
    ]
    if enc.vendor == "x265" and plan.source.video:
        color = plan.source.video[0].color
        if plan.profile.keep_hdr and color.is_hdr:
            x265_params: list[str] = []
            if color.primaries != "unknown":
                x265_params.append(f"colorprim={color.primaries}")
            if color.transfer != "unknown":
                x265_params.append(f"transfer={color.transfer}")
            if color.space != "unknown":
                x265_params.append(f"colormatrix={color.space}")
            if color.color_range != "unknown":
                x265_params.append(
                    f"range={'full' if color.color_range == 'pc' else 'limited'}"
                )
            if color.mastering_display:
                x265_params.append(f"master-display={color.mastering_display}")
            if color.max_cll is not None and color.max_fall is not None:
                x265_params.append(f"max-cll={color.max_cll},{color.max_fall}")
            if x265_params:
                result += ["-x265-params", ":".join(x265_params)]
    return result


def _max_bitrate_for(plan: Plan) -> int:
    v = plan.source.video[0] if plan.source.video else None
    base = v.bit_rate if (v and v.bit_rate) else 8_000_000
    return int(base * 1.25)


def build_encoder_capability_probe(plan: Plan) -> list[str]:
    """Build a short encode using this job's real output contract.

    The discovery probe in :mod:`core.encoder` intentionally stays cheap at
    640x360/yuv420p. This second-stage probe verifies the selected encoder at
    the planned resolution, pixel format, color tags, and rate-control mode so
    4K/10-bit/device-specific failures surface before segment processing.
    """
    if not plan.source.video:
        raise PipelineError("cannot probe encoder capability without a video stream")
    video = plan.source.video[0]
    width = max(2, video.width // 2 * 2)
    height = max(2, video.height // 2 * 2)
    tail_scale = _video_tail_scale(plan)
    match = re.fullmatch(r"scale=(\d+):(\d+)", tail_scale)
    if match is not None:
        width, height = int(match.group(1)), int(match.group(2))
    pix_fmt = _segment_pix_fmt(plan)
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=s={width}x{height}:r=24:d=0.25",
        "-vf", f"format={pix_fmt}",
        "-frames:v", "6",
    ]
    cmd += _encoder_args_for(plan)
    cmd += _color_output_args(plan)
    cmd += ["-an", "-f", "null", "-"]
    return cmd


def _loudnorm_params_from(
    profile: Profile, audio_tcs: list[TransformConfig],
) -> LoudnormParams:
    for tc in audio_tcs:
        if tc.id == LOUDNORM_ID:
            raw = dict(tc.params or {})
            raw.setdefault("integrated", profile.target_loudness_lufs)
            return LoudnormParams.model_validate(raw)
    return LoudnormParams(integrated=profile.target_loudness_lufs)


def expected_output_duration(plan: Plan) -> float:
    """Return the declared video timeline duration after playback-rate filters."""
    rate = 1.0
    for transform in plan.profile.transforms:
        if transform.enabled and transform.id == "video.speed":
            raw_rate = transform.params.get("rate", 1.0)
            if not isinstance(raw_rate, (int, float)):
                raise PipelineError("video.speed.rate must be numeric")
            rate *= float(raw_rate)
    return plan.source.duration_sec / rate


def compute_plan_hash(
    source: SourceMeta, profile: Profile, encoder: EncoderCandidate
) -> str:
    """Deterministic 16-hex hash of the (content + profile + encoder) triple."""
    source_meta = source.model_dump(mode="json", exclude={"path"})
    payload: dict[str, Any] = {
        # Include the complete probed topology (all video/audio/subtitle streams,
        # chapters, container and HDR fields), not only the first video's codec
        # and dimensions.  Resume artifacts are unsafe when any of those change.
        "source": source_meta,
        "source_content": _source_content_fingerprint(source.path),
        # mode="json" forces pydantic to coerce Path / Enum / datetime fields
        # to their JSON-native form (str / member-name / ISO). Without this,
        # a profile containing e.g. `BlendBParams.b_video_path: Path` would
        # leave a raw `Path` in the dict and json.dumps would fall back to
        # `default=str` — and `str(Path(...))` is platform-dependent
        # (backslashes on Windows). That gives a different plan_hash for the
        # same logical profile across OSes, breaking resume in cross-platform
        # batch runs.
        "profile": profile.model_dump(mode="json"),
        "encoder": encoder.name,
        "tool_version": __version__,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_FULL_HASH_LIMIT = 64 * 1024 * 1024
_SAMPLE_SIZE = 1024 * 1024
_SAMPLE_COUNT = 9


def _source_content_fingerprint(path: Path) -> dict[str, Any]:
    """Return a path-independent input identity suitable for resume keys.

    Files up to 64 MiB are hashed completely.  Larger media files use nine
    evenly-spaced 1 MiB samples plus their exact size: this bounds plan startup
    I/O while detecting ordinary in-place replacements throughout a movie, not
    just changes in its head/tail.  The full probed stream topology is hashed by
    :func:`compute_plan_hash` alongside this value.

    A non-existent path is supported for pure model/property tests; real plans
    are built only after :func:`probe` has verified the input exists.
    """
    try:
        stat = path.stat()
    except OSError:
        return {"scheme": "missing-v1", "path": str(path)}

    digest = hashlib.blake2b(digest_size=32)
    size = stat.st_size
    digest.update(size.to_bytes(16, "big", signed=False))
    try:
        with path.open("rb") as source_file:
            if size <= _FULL_HASH_LIMIT:
                while chunk := source_file.read(_SAMPLE_SIZE):
                    digest.update(chunk)
                scheme = "full-blake2b-v1"
            else:
                max_offset = max(0, size - _SAMPLE_SIZE)
                offsets = {
                    round(max_offset * index / (_SAMPLE_COUNT - 1))
                    for index in range(_SAMPLE_COUNT)
                }
                for offset in sorted(offsets):
                    source_file.seek(offset)
                    digest.update(offset.to_bytes(16, "big", signed=False))
                    digest.update(source_file.read(_SAMPLE_SIZE))
                scheme = "sampled-blake2b-v1"
    except OSError as exc:
        raise PipelineError(f"cannot fingerprint input for safe resume: {path}: {exc}") from exc
    return {"scheme": scheme, "size": size, "digest": digest.hexdigest()}
