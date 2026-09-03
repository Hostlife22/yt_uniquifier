"""Final media-contract validation for production outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.auxiliary_streams import AuxiliaryStream, get_auxiliary_streams
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Container, Plan, SourceMeta
from yt_uniquifier.core.pipeline import expected_output_duration
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices
from yt_uniquifier.core.transforms.hdr_wrap import is_tonemap_active

_OUTPUT_SUFFIXES: dict[Container, frozenset[str]] = {
    "mp4": frozenset({".mp4", ".m4v"}),
    "mov": frozenset({".mov"}),
    "mkv": frozenset({".mkv"}),
}


def allowed_output_suffixes(container: Container) -> frozenset[str]:
    """Return filename suffixes compatible with a profile container."""
    return _OUTPUT_SUFFIXES[container]


@dataclass(frozen=True)
class MediaInvariantFailure:
    code: str
    expected: object
    actual: object


@dataclass(frozen=True)
class MediaInvariantReport:
    output: Path
    failures: tuple[MediaInvariantFailure, ...]

    @property
    def valid(self) -> bool:
        return not self.failures


def inspect_output_contract(
    plan: Plan,
    output: Path,
    *,
    probed_output: SourceMeta | None = None,
) -> MediaInvariantReport:
    """Probe *output* and compare non-negotiable source→output invariants."""
    failures: list[MediaInvariantFailure] = []
    try:
        result = probed_output if probed_output is not None else probe(output)
    except Exception as exc:  # noqa: BLE001 - normalize probe failures
        return MediaInvariantReport(
            output=output,
            failures=(MediaInvariantFailure("output.probe", "valid media", str(exc)),),
        )

    if len(result.video) != 1:
        failures.append(MediaInvariantFailure("streams.video", 1, len(result.video)))

    expected_audio = len(selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    ))
    if len(result.audio) != expected_audio:
        failures.append(MediaInvariantFailure(
            "streams.audio", expected_audio, len(result.audio),
        ))
    else:
        selected_audio = selected_audio_relative_indices(
            plan.source, plan.profile.audio_tracks,
        )
        audio_has_default = any(plan.source.audio[index].is_default for index in selected_audio)
        for output_idx, source_idx in enumerate(selected_audio):
            expected_stream = plan.source.audio[source_idx]
            actual_stream = result.audio[output_idx]
            _compare_stream_metadata(
                failures,
                kind="audio",
                index=output_idx,
                expected_stream=expected_stream,
                actual_stream=actual_stream,
                output_container=plan.profile.output_container,
                muxer_adds_default=not audio_has_default and output_idx == 0,
            )

    expected_subtitles = len(plan.source.subtitle)
    if len(result.subtitle) != expected_subtitles:
        failures.append(MediaInvariantFailure(
            "streams.subtitle", expected_subtitles, len(result.subtitle),
        ))
    else:
        subtitle_has_default = any(stream.is_default for stream in plan.source.subtitle)
        for index, (expected_subtitle, actual_subtitle) in enumerate(zip(
            plan.source.subtitle, result.subtitle, strict=True,
        )):
            _compare_stream_metadata(
                failures,
                kind="subtitle",
                index=index,
                expected_stream=expected_subtitle,
                actual_stream=actual_subtitle,
                output_container=plan.profile.output_container,
                muxer_adds_default=not subtitle_has_default and index == 0,
            )

    expected_chapters = len(plan.source.chapters)
    if len(result.chapters) != expected_chapters:
        failures.append(MediaInvariantFailure(
            "chapters.count", expected_chapters, len(result.chapters),
        ))

    _compare_auxiliary_streams(
        failures,
        get_auxiliary_streams(plan.source),
        get_auxiliary_streams(result),
    )

    duration_expected = expected_output_duration(plan)
    fps = plan.source.video[0].fps if plan.source.video else 25.0
    # MP4 edit lists and AAC priming can make probed container/stream duration
    # differ from the decodable frame timeline by several frames. Keep this
    # strict enough to catch the confirmed 1.021 s shift while avoiding false
    # rejection of sources whose own ffprobe duration metadata is approximate.
    duration_tolerance = max(0.25, 3.0 / max(fps, 1.0))
    if abs(result.duration_sec - duration_expected) > duration_tolerance:
        failures.append(MediaInvariantFailure(
            "timeline.duration",
            round(duration_expected, 6),
            round(result.duration_sec, 6),
        ))

    first_video_pts = result._first_video_pts_sec
    start_tolerance = max(0.1, 1.5 / max(fps, 1.0))
    if first_video_pts is not None and abs(first_video_pts) > start_tolerance:
        failures.append(MediaInvariantFailure(
            "timeline.video_start",
            0.0,
            round(first_video_pts, 6),
        ))

    if plan.source.video and result.video:
        _compare_color_contract(failures, plan, result.video[0].color)

    return MediaInvariantReport(output=output, failures=tuple(failures))


def _compare_auxiliary_streams(
    failures: list[MediaInvariantFailure],
    expected: tuple[AuxiliaryStream, ...],
    actual: tuple[AuxiliaryStream, ...],
) -> None:
    for kind in ("attachment", "data", "attached_pic"):
        expected_kind = [stream for stream in expected if stream.kind == kind]
        actual_kind = [stream for stream in actual if stream.kind == kind]
        if len(expected_kind) != len(actual_kind):
            failures.append(MediaInvariantFailure(
                f"streams.{kind}", len(expected_kind), len(actual_kind),
            ))
            continue
        fields: tuple[str, ...]
        if kind == "attachment":
            fields = ("codec", "codec_tag", "filename", "mimetype", "title")
        elif kind == "data":
            fields = ("codec", "codec_tag", "language", "title", "timecode")
        else:
            fields = ("codec", "title")
        for index, (expected_stream, actual_stream) in enumerate(zip(
            expected_kind, actual_kind, strict=True,
        )):
            for field_name in fields:
                expected_value = getattr(expected_stream, field_name)
                actual_value = getattr(actual_stream, field_name)
                if expected_value != actual_value:
                    failures.append(MediaInvariantFailure(
                        f"streams.{kind}.{index}.{field_name}",
                        expected_value,
                        actual_value,
                    ))


def _compare_color_contract(
    failures: list[MediaInvariantFailure],
    plan: Plan,
    actual_color: object,
) -> None:
    """Validate the declared SDR, HDR-preserve, or HDR→SDR output contract."""
    source_color = plan.source.video[0].color
    tonemapped = is_tonemap_active(plan.profile.transforms)
    keeping_hdr = source_color.is_hdr and plan.profile.keep_hdr and not tonemapped

    if tonemapped:
        expected_fields: dict[str, object] = {
            "is_hdr": False,
            "transfer": "bt709",
            "primaries": "bt709",
            "space": "bt709",
            "color_range": "tv",
            "bit_depth": 8,
        }
    else:
        # Every non-tonemap path ends in yuv420p (SDR) or yuv420p10le
        # (preserved HDR), regardless of a source decoder's native depth.
        expected_fields = {
            "is_hdr": keeping_hdr,
            "bit_depth": 10 if keeping_hdr else 8,
        }
        for field_name in ("transfer", "primaries", "space", "color_range"):
            source_value = getattr(source_color, field_name)
            if source_value != "unknown":
                expected_fields[field_name] = source_value

    if keeping_hdr:
        for field_name in (
            "mastering_display", "max_cll", "max_fall", "dynamic_metadata",
        ):
            expected_fields[field_name] = getattr(source_color, field_name)

    for field_name, expected in expected_fields.items():
        actual = getattr(actual_color, field_name)
        if (
            field_name == "color_range"
            and expected == "tv"
            and actual == "unknown"
            and not tonemapped
            and source_color.transfer == "unknown"
            and source_color.primaries == "unknown"
            and source_color.space == "unknown"
        ):
            # In H.264/H.265, absent VUI means the normal limited-range
            # interpretation. FFprobe reports that as ``unknown`` when every
            # other color descriptor is also absent; do not reject an otherwise
            # valid untagged legacy source for failing to invent a colorimetry.
            continue
        if actual != expected:
            failures.append(MediaInvariantFailure(
                f"color.{field_name}", expected, actual,
            ))


def _compare_stream_metadata(
    failures: list[MediaInvariantFailure],
    *,
    kind: str,
    index: int,
    expected_stream: object,
    actual_stream: object,
    output_container: str,
    muxer_adds_default: bool,
) -> None:
    for field_name in ("language", "title", "is_default", "dispositions"):
        expected = getattr(expected_stream, field_name)
        actual = getattr(actual_stream, field_name)
        if field_name == "language":
            # MP4/MOV commonly synthesizes ISO 639 ``und`` while Matroska
            # omits the tag for the same semantic state: language unspecified.
            # Treating that container boilerplate as metadata corruption makes
            # valid MKV -> MP4 processing (including calibration probes) fail.
            expected = _normalize_language(expected)
            actual = _normalize_language(actual)
        if field_name == "is_default" and output_container in {"mp4", "mov"}:
            expected = bool(expected or muxer_adds_default)
        if field_name == "dispositions":
            expected_flags = set(expected)
            if output_container in {"mp4", "mov"}:
                # ISO BMFF has no representation for Matroska's full
                # disposition vocabulary. FFmpeg currently carries these
                # four and drops e.g. original/comment/lyrics.
                supported_flags = (
                    {"default"}
                    if output_container == "mov"
                    else {"default", "forced", "hearing_impaired", "visual_impaired"}
                )
                expected_flags &= supported_flags
                if muxer_adds_default:
                    expected_flags.add("default")
            expected = tuple(sorted(expected_flags))
            actual = tuple(sorted(actual))
        if actual != expected:
            failures.append(MediaInvariantFailure(
                f"streams.{kind}.{index}.{field_name}", expected, actual,
            ))


def _normalize_language(value: object) -> object:
    if value in {None, "", "und"}:
        return None
    return value


def require_output_contract(plan: Plan, output: Path) -> MediaInvariantReport:
    """Raise a typed pipeline error unless all final invariants hold."""
    report = inspect_output_contract(plan, output)
    if report.valid:
        return report
    details = "; ".join(
        f"{failure.code}: expected={failure.expected!r}, actual={failure.actual!r}"
        for failure in report.failures
    )
    raise PipelineError(f"final output failed media contract: {details}")
