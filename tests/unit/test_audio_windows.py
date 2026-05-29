"""Audio window planner for v0.4.2 divergent audio."""

from __future__ import annotations

from yt_uniquifier.core.audio_windows import (
    CROSSFADE_SEC,
    WINDOW_SEC,
    plan_windows,
)


def test_short_audio_one_window() -> None:
    """≤ 2 × WINDOW_SEC stays as a single window — no crossfade."""
    windows = plan_windows(45.0)
    assert len(windows) == 1
    assert windows[0].start_sec == 0.0
    assert windows[0].end_sec == 45.0
    assert windows[0].crossfade_in_sec == 0.0
    assert windows[0].crossfade_out_sec == 0.0


def test_2min_audio_two_windows() -> None:
    windows = plan_windows(120.0)
    assert len(windows) == 2
    assert windows[0].crossfade_in_sec == 0.0
    assert windows[0].crossfade_out_sec == CROSSFADE_SEC
    assert windows[1].crossfade_in_sec == CROSSFADE_SEC
    assert windows[1].crossfade_out_sec == 0.0


def test_5min_audio_five_windows_no_gaps() -> None:
    """Adjacent windows must be contiguous (no time lost between them)."""
    windows = plan_windows(300.0)
    assert len(windows) == 5
    for a, b in zip(windows[:-1], windows[1:], strict=True):
        assert abs(a.end_sec - b.start_sec) < 1e-6


def test_last_window_absorbs_remainder() -> None:
    """130 s gives 2 windows of 65 s each (not 60 + 60 + 10)."""
    windows = plan_windows(130.0)
    assert len(windows) == 2
    assert windows[-1].end_sec == 130.0


def test_2h_movie_120_windows() -> None:
    """Realistic feature-length: 2 h = 120 windows of 60 s each."""
    windows = plan_windows(2 * 60 * 60)
    assert len(windows) == 120
    assert windows[0].end_sec == WINDOW_SEC
    assert windows[-1].end_sec == 7200.0


def test_indices_sequential_from_zero() -> None:
    windows = plan_windows(300.0)
    assert [w.idx for w in windows] == [0, 1, 2, 3, 4]
