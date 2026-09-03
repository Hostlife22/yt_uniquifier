"""Real-FFmpeg regression for batched SSCD midpoint extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.qa.sscd import _extract_frames


@needs_ffmpeg
@pytest.mark.integration
def test_sscd_extracts_complete_midpoint_grid_in_one_batch(
    tiny_clip: Path,
    tmp_path: Path,
) -> None:
    frames = _extract_frames(tiny_clip, tmp_path / "frames", frame_count=8)

    assert len(frames) == 8
    assert [frame.name for frame in frames] == [
        f"frame_{idx:05d}.png" for idx in range(8)
    ]
    assert all(frame.stat().st_size > 0 for frame in frames)
