"""Final media-contract validation for production outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.pipeline import expected_output_duration
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices


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


def inspect_output_contract(plan: Plan, output: Path) -> MediaInvariantReport:
    """Probe *output* and compare non-negotiable source→output invariants."""
    failures: list[MediaInvariantFailure] = []
    try:
        result = probe(output)
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

    expected_subtitles = len(plan.source.subtitle)
    if len(result.subtitle) != expected_subtitles:
        failures.append(MediaInvariantFailure(
            "streams.subtitle", expected_subtitles, len(result.subtitle),
        ))

    expected_chapters = len(plan.source.chapters)
    if len(result.chapters) != expected_chapters:
        failures.append(MediaInvariantFailure(
            "chapters.count", expected_chapters, len(result.chapters),
        ))

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

    if plan.profile.keep_hdr and plan.source.video and plan.source.video[0].color.is_hdr:
        output_is_hdr = bool(result.video and result.video[0].color.is_hdr)
        if not output_is_hdr:
            failures.append(MediaInvariantFailure("color.hdr", True, False))

    return MediaInvariantReport(output=output, failures=tuple(failures))


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
