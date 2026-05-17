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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_uniquifier import __version__
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
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
from yt_uniquifier.core.transforms.base import LabelAllocator
from yt_uniquifier.core.transforms.video_blend import B_INPUT_PLACEHOLDER
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

LOUDNORM_ID = "audio.loudnorm"
BLEND_B_ID = "video.blend_b"


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
        video_transforms = self._enabled(kind="video")
        audio_transforms = self._enabled(kind="audio")

        # ---- video chain ----
        v_in = "0:v:0"
        v_chains: list[str] = []
        v_label = v_in
        extra_inputs: list[Path] = []

        for tc in video_transforms:
            spec = get(tc.id)
            params = spec.schema.model_validate({**spec.defaults, **tc.params})
            chain = spec.build(params, self.alloc, v_label)
            v_chains.append(f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]")
            v_label = chain.out_label
            extra_inputs.extend(Path(p) for p in chain.extra_inputs)

        # Tail: round dims to even (required by libx264/H.264 profiles) and set pix_fmt.
        pix_fmt = self._target_pix_fmt()
        v_out = self.alloc.next("v")
        v_chains.append(
            f"[{v_label}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt}[{v_out}]"
        )

        # ---- audio chain (main track only) ----
        loudnorm_used = any(tc.id == LOUDNORM_ID for tc in audio_transforms)
        if loudnorm_used and self._loudnorm_measurement is None:
            params = self._loudnorm_params(audio_transforms)
            self._loudnorm_measurement = measure(self.plan.source.path, params)

        a_in = "0:a:0"
        a_chains: list[str] = []
        a_label: str | None = a_in
        if not self.plan.source.audio:
            a_label = None
        else:
            for tc in audio_transforms:
                spec = get(tc.id)
                params = spec.schema.model_validate({**spec.defaults, **tc.params})
                if tc.id == LOUDNORM_ID:
                    assert isinstance(params, LoudnormParams)
                    assert self._loudnorm_measurement is not None
                    chain = build_apply(
                        params, self._loudnorm_measurement, self.alloc, a_label or a_in
                    )
                else:
                    chain = spec.build(params, self.alloc, a_label or a_in)
                a_chains.append(
                    f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]"
                )
                a_label = chain.out_label

        # ---- assemble filter_complex ----
        # blend_b uses B_INPUT_PLACEHOLDER which must be replaced with concrete input refs.
        all_chains = v_chains + a_chains
        filter_complex = ";".join(all_chains)
        for idx, _path in enumerate(extra_inputs, start=1):
            filter_complex = filter_complex.replace(
                f"[{B_INPUT_PLACEHOLDER}]", f"[{idx}:v]", 1
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
        # Stream index per-output; the main processed audio is :a:0.
        # Use absolute audio-stream specifiers from source via :a:<N> with N starting at 1.
        for n, _src_idx in enumerate(indices_for_passthrough, start=1):
            # Map by source-stream index from the original container.
            args += ["-map", f"0:a:{n}?"]
        # Codec copies for those outputs.
        for n in range(1, len(indices_for_passthrough) + 1):
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
        return [
            "-map_metadata", "-1",
            "-metadata", f"encoder=yt-uniquifier/{__version__}",
        ]


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
    video_transforms = [
        tc for tc in plan.profile.transforms
        if tc.enabled and get(tc.id).kind == "video"
    ]

    v_label = "0:v:0"
    v_chains: list[str] = []
    extra_inputs: list[Path] = []
    for tc in video_transforms:
        spec = get(tc.id)
        params = spec.schema.model_validate({**spec.defaults, **tc.params})
        chain = spec.build(params, alloc, v_label)
        v_chains.append(f"[{chain.in_label}]{chain.filter_str}[{chain.out_label}]")
        v_label = chain.out_label
        extra_inputs.extend(Path(p) for p in chain.extra_inputs)

    pix_fmt = _segment_pix_fmt(plan)
    v_out = alloc.next("v")
    v_chains.append(
        f"[{v_label}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt}[{v_out}]"
    )

    filter_complex = ";".join(v_chains)
    for idx, _ in enumerate(extra_inputs, start=1):
        filter_complex = filter_complex.replace(
            f"[{B_INPUT_PLACEHOLDER}]", f"[{idx}:v]", 1
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
            assert measurement is not None
            chain = build_apply(ln_params, measurement, alloc, a_label)
        else:
            chain = spec.build(params, alloc, a_label)
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


def _segment_pix_fmt(plan: Plan) -> str:
    if not plan.source.video:
        return "yuv420p"
    v = plan.source.video[0]
    if v.color.is_hdr and plan.profile.keep_hdr:
        return "yuv420p10le"
    return "yuv420p"


def _encoder_args_for(plan: Plan) -> list[str]:
    """Same encoder args as FilterGraph._encoder_args, but free function."""
    enc = plan.encoder
    name = enc.name
    if enc.vendor == "nvenc":
        mb = _max_bitrate_for(plan)
        return [
            "-c:v", name, "-preset", "p6", "-rc", "vbr", "-cq", "19",
            "-b:v", "0", "-maxrate", str(mb), "-bufsize", str(mb * 2),
        ]
    if enc.vendor == "qsv":
        return ["-c:v", name, "-global_quality", "19", "-look_ahead", "1"]
    if enc.vendor == "amf":
        return ["-c:v", name, "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]
    if enc.vendor == "videotoolbox":
        return ["-c:v", name, "-q:v", "50"]
    return ["-c:v", name, "-preset", "slow", "-crf", "18"]


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
        "profile": profile.model_dump(),
        "encoder": encoder.name,
        "tool_version": __version__,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
