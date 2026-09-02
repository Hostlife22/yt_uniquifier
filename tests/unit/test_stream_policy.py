from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import AudioStream, SourceMeta
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices


def _source() -> SourceMeta:
    return SourceMeta(
        path=Path("source.mkv"), container="mkv", duration_sec=1, size_bytes=1,
        audio=[
            AudioStream(index=1, codec="aac", sample_rate=48_000, channels=2),
            AudioStream(index=3, codec="opus", sample_rate=48_000, channels=2),
            AudioStream(index=7, codec="aac", sample_rate=44_100, channels=1),
        ],
    )


def test_first_and_all_selection() -> None:
    assert selected_audio_relative_indices(_source(), "first") == [0]
    assert selected_audio_relative_indices(_source(), "all") == [0, 1, 2]


def test_explicit_selection_uses_absolute_probe_indices_and_keeps_order() -> None:
    assert selected_audio_relative_indices(_source(), [7, 3]) == [2, 1]


def test_unknown_explicit_stream_is_rejected() -> None:
    with pytest.raises(PipelineError, match="available audio stream indices"):
        selected_audio_relative_indices(_source(), [99])
