"""Shared, UI-independent stream selection policy."""

from __future__ import annotations

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import AudioTracksOpt, SourceMeta


def selected_audio_relative_indices(
    source: SourceMeta,
    selection: AudioTracksOpt | list[int],
) -> list[int]:
    """Resolve a profile selection to FFmpeg audio-relative indices.

    Explicit integer selections are absolute stream indices reported by
    ffprobe/``AudioStream.index``. The result is ordered exactly as requested;
    its first element is the track processed by audio transforms.
    """
    if not source.audio:
        return []
    if selection == "first":
        return [0]
    if selection == "all":
        return list(range(len(source.audio)))

    by_absolute_index = {
        stream.index: relative for relative, stream in enumerate(source.audio)
    }
    result: list[int] = []
    for absolute_index in selection:
        relative = by_absolute_index.get(absolute_index)
        if relative is None:
            available = sorted(by_absolute_index)
            raise PipelineError(
                f"audio_tracks references stream index {absolute_index}, "
                f"available audio stream indices are {available}"
            )
        if relative not in result:
            result.append(relative)
    return result
