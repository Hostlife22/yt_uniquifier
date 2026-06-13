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
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

LOUDNORM_ID = "audio.loudnorm"
BLEND_B_ID = "video.blend_b"


def _wrap_chain_str(in_label: str, filter_str: str, out_label: str) -> str:
    """Wrap a builder's filter_str with the standard ``[in][filter][out]``.

    Multi-input transforms (currently only ``video.blend_b``) include the
    ``__IN__`` placeholder in their filter_str so they can position the
    primary stream after a secondary input (e.g. ``[B][__IN__]scale2ref…``).
    For those, the wrap only substitutes ``[__IN__]`` and emits the
    filter_str raw — the builder is responsible for its own trailing
    ``[out_label]``. Single-input transforms get the legacy wrap.
    """
    if f"[{IN_PLACEHOLDER}]" in filter_str:
        return filter_str.replace(f"[{IN_PLACEHOLDER}]", f"[{in_label}]")
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
        rng = random.Random(self.plan.run_seed)

        # ---- video chain ----
        # Delegated to the module-level helper so the per-segment path
        # (build_video_segment_command) and full-file path produce
        # identical HDR / color handling.
        v_label, v_chains, extra_inputs = _build_video_chain(
            self.plan, self.alloc, "0:v:0", rng,
        )

        # Tail: round dims to even (required by libx264/H.264 profiles) and set pix_fmt.
        pix_fmt = self._target_pix_fmt()
        v_out = self.alloc.next("v")
        v_chains.append(
            f"[{v_label}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt}[{v_out}]"
        )

        # ---- audio chain (main track only) ----
        loudnorm_used = any(tc.id == LOUDNORM_ID for tc in audio_transforms)
        if loudnorm_used and self._loudnorm_measurement is None:
            # Use a distinct local so the in-loop `params: BaseModel` below
            # isn't narrowed to LoudnormParams by mypy's flow analysis.
            ln_params = self._loudnorm_params(audio_transforms)
            self._loudnorm_measurement = measure(self.plan.source.path, ln_params)

        a_chains: list[str] = []
        a_label: str | None = None
        if self.plan.source.audio:
            # `0:a:0` is the ffmpeg *relative* audio specifier — "first
            # audio stream of input 0", regardless of absolute container
            # index. The dead `or a_in` fallback that used to live here
            # is removed: `a_label` starts truthy and is reassigned to
            # `chain.out_label` (also truthy) on every iteration.
            a_label = "0:a:0"
            for tc in audio_transforms:
                spec = get(tc.id)
                params = spec.schema.model_validate({**spec.defaults, **tc.params})
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
                    chain = build_apply(
                        params, self._loudnorm_measurement, self.alloc,
                        a_label, rng=rng,
                    )
                else:
                    chain = call_build(spec, params, self.alloc, a_label, rng=rng)
                a_chains.append(
                    f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]"
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
            args += ["-map", f"[{a_label}]"]

        passthrough_audio_maps = self._build_audio_passthrough()
        args += passthrough_audio_maps

        passthrough_sub_maps = self._build_subtitle_passthrough()
        args += passthrough_sub_maps

        if self.plan.source.chapters:
            args += ["-map_chapters", "0"]
        else:
            args += ["-map_chapters", "-1"]

        args += self._encoder_args()
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
        for tc in audio_tcs:
            if tc.id == LOUDNORM_ID:
                return LoudnormParams.model_validate(tc.params or {})
        return LoudnormParams()

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
        all_audio = self.plan.source.audio
        if len(all_audio) <= 1:
            return []
        opt = self.plan.profile.audio_tracks
        if opt == "first":
            indices_for_passthrough = [a.index for a in all_audio[1:]]
        elif opt == "all":
            # "all" = main track processed + everything else copied; main is index 0 audio.
            indices_for_passthrough = [a.index for a in all_audio[1:]]
        elif isinstance(opt, list):
            # explicit indices: those NOT chosen as main (0) are passthrough.
            indices_for_passthrough = [i for i in opt if i != all_audio[0].index]
        else:  # pragma: no cover - unreachable per type system
            indices_for_passthrough = []

        args: list[str] = []
        # `src_idx` is the audio-relative index of each passthrough track in
        # the source container (e.g. tracks [1, 3]); `n` is just its slot in
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
        # Skip image-based subs in mp4 (they don't fit).
        any_text = any(not s.is_image_based for s in self.plan.source.subtitle)
        if not any_text:
            return ["-sn"]
        return ["-map", "0:s?", "-c:s", "copy"]

    def _encoder_args(self) -> list[str]:
        enc = self.plan.encoder
        name = enc.name
        if enc.vendor == "nvenc":
            return [
                "-c:v", name, "-preset", "p6", "-rc", "vbr", "-cq", "19",
                "-b:v", "0", "-maxrate", str(self._max_bitrate()),
                "-bufsize", str(self._max_bitrate() * 2),
            ]
        if enc.vendor == "qsv":
            return ["-c:v", name, "-global_quality", "19", "-look_ahead", "1"]
        if enc.vendor == "amf":
            return ["-c:v", name, "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]
        if enc.vendor == "videotoolbox":
            return ["-c:v", name, "-q:v", "50"]
        # x264 / x265
        return ["-c:v", name, "-preset", "slow", "-crf", "18"]

    def _max_bitrate(self) -> int:
        v = self.plan.source.video[0] if self.plan.source.video else None
        base = v.bit_rate if (v and v.bit_rate) else 8_000_000
        return int(base * 1.25)

    def _audio_output_args(self, *, has_main: bool) -> list[str]:
        if not has_main:
            return []
        return ["-c:a:0", "aac", "-b:a:0", "256k"]

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
        return ["-map_metadata", "-1"]


def build_video_segment_command(
    plan: Plan,
    segment_input: Path,
    segment_output: Path,
) -> BuiltCommand:
    """Build an ffmpeg command that applies video transforms to one segment.

    The segment file is the input (not the original source). Audio in the
    segment is passed through with stream copy — main audio is handled
    separately by build_main_audio_command on the full source.
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
        f"[{v_label}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt}[{v_out}]"
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
    # Copy all audio + subs from the segment as-is.
    args += ["-map", "0:a?", "-c:a", "copy"]
    args += ["-map", "0:s?", "-c:s", "copy"]
    args += ["-map_chapters", "-1"]
    args += _encoder_args_for(plan)
    args += ["-map_metadata", "-1"]
    args += [str(segment_output)]

    return BuiltCommand(
        args=args,
        filter_complex=filter_complex,
        output_video_label=v_out,
        output_audio_label=None,
        passthrough_audio_maps=["-map", "0:a?", "-c:a", "copy"],
        passthrough_sub_maps=["-map", "0:s?", "-c:s", "copy"],
        extra_inputs=extra_inputs,
    )


def build_video_segment_command_fused(
    plan: Plan,
    segment: Segment,
    source: Path,
    segment_output: Path,
) -> BuiltCommand:
    """B3 (v0.6.0): fused single-fork variant of build_video_segment_command.

    Reads directly from the source with `-ss/-t` input seek (keyframe-
    aligned) and applies the filter_complex in the same ffmpeg fork.
    Eliminates the intermediate ``seg_NNNN_src.mkv`` stream-copy file
    that the legacy two-fork path produced — saves ~600 MB peak disk
    per 1080p segment and cuts per-segment wall time by 15-25 % on
    HDD I/O-bound runs.

    PTS handling matches ``stream_copy_extract`` exactly:
      - ``-ss <start>`` BEFORE ``-i`` for input seek (keyframe-aligned)
      - ``-t <span>`` AFTER ``-i`` to clamp duration
      - ``-avoid_negative_ts make_zero`` to anchor output PTS at 0
    CRIT-2 (2026-05-30 test report) — we use ``-t``, not ``-to``,
    because some MP4 sources carry packet PTS extending past
    ``container.duration``.

    Audio and subtitles are stream-copied from the source's segment
    window, matching the legacy behaviour. The concat step replaces
    the segment's first audio track with the separately-processed
    ``main_audio`` (loudnorm, pitch, etc.), so the segment-level audio
    here is just a placeholder for concat-demuxer stream-layout
    consistency.
    """
    alloc = LabelAllocator()
    rng = random.Random(plan.run_seed)
    v_label, v_chains, extra_inputs = _build_video_chain(
        plan, alloc, "0:v:0", rng,
    )

    pix_fmt = _segment_pix_fmt(plan)
    v_out = alloc.next("v")
    v_chains.append(
        f"[{v_label}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt}[{v_out}]"
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
        # `-avoid_negative_ts make_zero` below anchors the output PTS
        # at 0 so concat works without per-segment PTS rewriting.
        "-ss", f"{segment.start_sec:.6f}",
        "-i", str(source),
        "-t", f"{span:.6f}",
        "-avoid_negative_ts", "make_zero",
    ]
    for p in extra_inputs:
        args += ["-i", str(p)]

    args += ["-filter_complex", filter_complex]
    args += ["-map", f"[{v_out}]"]
    # Stream-copy audio + subs from the source's segment window —
    # concat will overwrite track 0 with main_audio anyway, but the
    # concat demuxer needs the per-segment stream layout to match.
    args += ["-map", "0:a?", "-c:a", "copy"]
    args += ["-map", "0:s?", "-c:s", "copy"]
    args += ["-map_chapters", "-1"]
    args += _encoder_args_for(plan)
    args += ["-map_metadata", "-1"]
    args += [str(segment_output)]

    return BuiltCommand(
        args=args,
        filter_complex=filter_complex,
        output_video_label=v_out,
        output_audio_label=None,
        passthrough_audio_maps=["-map", "0:a?", "-c:a", "copy"],
        passthrough_sub_maps=["-map", "0:s?", "-c:s", "copy"],
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
    if not audio_transforms or not plan.source.audio:
        # Nothing to process; signal caller to skip.
        return (
            BuiltCommand(args=[], filter_complex="", output_video_label=""),
            loudnorm_measurement,
        )

    measurement = loudnorm_measurement
    needs_loudnorm = any(tc.id == LOUDNORM_ID for tc in audio_transforms)
    if needs_loudnorm and measurement is None:
        ln_params = _loudnorm_params_from(audio_transforms)
        measurement = measure(plan.source.path, ln_params)

    a_label = "0:a:0"
    a_chains: list[str] = []
    for tc in audio_transforms:
        spec = get(tc.id)
        params = spec.schema.model_validate({**spec.defaults, **tc.params})
        if tc.id == LOUDNORM_ID:
            ln_params = LoudnormParams.model_validate({**spec.defaults, **tc.params})
            if measurement is None:
                raise PipelineError(
                    "loudnorm: measurement missing — pass-1 scan "
                    "was skipped before build_main_audio_command()",
                )
            chain = build_apply(ln_params, measurement, alloc, a_label, rng=rng)
        else:
            chain = call_build(spec, params, alloc, a_label, rng=rng)
        a_chains.append(f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]")
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
        "-c:a", "aac", "-b:a", "256k",
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

    if not audio_transforms_all or not plan.source.audio:
        return (
            BuiltCommand(args=[], filter_complex="", output_video_label=""),
            loudnorm_measurement,
        )

    # Loudnorm measurement happens once on the full source — same as legacy path.
    measurement = loudnorm_measurement
    if needs_loudnorm and measurement is None:
        ln_params = _loudnorm_params_from(audio_transforms_all)
        measurement = measure(plan.source.path, ln_params)

    alloc = LabelAllocator()
    window_chains: list[str] = []
    window_out_labels: list[str] = []
    # Relative audio specifier — same as FilterGraph.build.
    main_audio_specifier = "0:a:0"

    for w in windows:
        seg_seed = derive_segment_seed(
            plan.plan_hash, AUDIO_WINDOW_NS_OFFSET + w.idx, plan.run_seed,
        )
        win_rng = random.Random(seg_seed)
        cut_in = max(0.0, w.start_sec - w.crossfade_in_sec)
        cut_out = min(plan.source.duration_sec, w.end_sec + w.crossfade_out_sec)

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
            params = spec.schema.model_validate({**spec.defaults, **tc.params})
            chain = call_build(spec, params, alloc, a_label, rng=win_rng)
            chain_parts.append(
                f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]"
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

    # Global loudnorm on the concatenated stream.
    if needs_loudnorm:
        ln_defaults = get(LOUDNORM_ID).defaults
        ln_from_profile = _loudnorm_params_from(audio_transforms_all).model_dump()
        ln_params = LoudnormParams.model_validate({**ln_defaults, **ln_from_profile})
        if measurement is None:
            raise PipelineError(
                "loudnorm: measurement missing — pass-1 scan was "
                "skipped before build_main_audio_command_windowed()",
            )
        # Derive a dedicated seed for the loudnorm jitter so each run/file
        # under the `divergent` strategy gets a unique target jitter value.
        # Using `plan.run_seed` raw made the per-call jitter constant
        # across runs that shared the same seed but were otherwise
        # supposed to diverge.
        ln_seed = derive_segment_seed(
            plan.plan_hash, AUDIO_WINDOW_NS_OFFSET - 1, plan.run_seed,
        )
        ln_chain = build_apply(
            ln_params, measurement, alloc, accumulator,
            rng=random.Random(ln_seed),
        )
        final_label = ln_chain.out_label
        loudnorm_str = f"[{ln_chain.in_label}]{ln_chain.filter_str}[{ln_chain.out_label}]"
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
        "-c:a", "aac", "-b:a", "256k",
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


def _encoder_args_for(plan: Plan) -> list[str]:
    """Same encoder args as FilterGraph._encoder_args, but free function."""
    enc = plan.encoder
    name = enc.name
    mb = _max_bitrate_for(plan)
    if enc.vendor == "nvenc":
        return [
            "-c:v", name, "-preset", "p6", "-rc", "vbr", "-cq", "19",
            "-b:v", "0", "-maxrate", str(mb), "-bufsize", str(mb * 2),
        ]
    if enc.vendor == "qsv":
        return ["-c:v", name, "-global_quality", "19", "-look_ahead", "1"]
    if enc.vendor == "amf":
        return ["-c:v", name, "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]
    if enc.vendor == "videotoolbox":
        return [
            "-c:v", name, "-b:v", str(mb),
            "-maxrate", str(int(mb * 1.5)), "-bufsize", str(mb * 2),
            "-allow_sw", "1",
        ]
    return [
        "-c:v", name, "-preset", "medium", "-crf", "18",
        "-maxrate", str(mb), "-bufsize", str(mb * 2),
    ]


def _max_bitrate_for(plan: Plan) -> int:
    v = plan.source.video[0] if plan.source.video else None
    base = v.bit_rate if (v and v.bit_rate) else 8_000_000
    return int(base * 1.25)


def _loudnorm_params_from(audio_tcs: list[TransformConfig]) -> LoudnormParams:
    for tc in audio_tcs:
        if tc.id == LOUDNORM_ID:
            return LoudnormParams.model_validate(tc.params or {})
    return LoudnormParams()


def compute_plan_hash(
    source: SourceMeta, profile: Profile, encoder: EncoderCandidate
) -> str:
    """Deterministic 16-hex hash of the (content + profile + encoder) triple."""
    payload: dict[str, Any] = {
        "source": {
            "path": str(source.path),
            "size": source.size_bytes,
            "duration": round(source.duration_sec, 3),
            "video_codec": source.video[0].codec if source.video else "",
            "video_dims": (
                [source.video[0].width, source.video[0].height] if source.video else []
            ),
        },
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
