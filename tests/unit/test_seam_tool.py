"""Regression tests for source/output concat-seam registration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import seam_test


def test_ssim_pair_compares_two_inputs_and_resets_pts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        captured.extend(command)
        return SimpleNamespace(returncode=0, stderr="SSIM All:0.9876 (19.2dB)\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    score = seam_test._ssim_pair(
        tmp_path / "source.mp4",
        tmp_path / "output.mp4",
        source_start_sec=9.5,
        output_start_sec=10.0,
        frames=8,
    )

    assert score == pytest.approx(0.9876)
    assert captured.count("-i") == 2
    assert "9.500000" in captured
    assert "10.000000" in captured
    graph = captured[captured.index("-filter_complex") + 1]
    assert graph.count("setpts=PTS-STARTPTS") == 2
    assert "[dist][ref]ssim" in graph


def test_registered_window_selects_best_bounded_frame_offset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scores = {-2: 0.8, -1: 0.9, 0: 0.95, 1: 0.99, 2: 0.85}

    def fake_pair(
        _source: Path,
        _output: Path,
        *,
        source_start_sec: float,
        output_start_sec: float,
        frames: int,
    ) -> float:
        assert frames == 8
        offset = round((source_start_sec - output_start_sec) * 25)
        return scores[offset]

    monkeypatch.setattr(seam_test, "_ssim_pair", fake_pair)
    result = seam_test._registered_ssim_window(
        tmp_path / "source.mp4",
        tmp_path / "output.mp4",
        start_sec=10.0,
        frames=8,
        fps=25.0,
        search_frames=2,
    )

    assert result == seam_test.RegisteredScore(score=0.99, source_offset_frames=1)


def test_seam_control_is_centered_in_previous_segment() -> None:
    windows = seam_test._seam_windows(
        [
            {"idx": 0, "start_sec": 10.0, "end_sec": 20.0},
            {"idx": 1, "start_sec": 20.0, "end_sec": 30.0},
        ],
        frames=8,
        fps=4.0,
    )

    assert windows == [
        seam_test.SeamWindow(
            boundary_sec=20.0,
            seam_start_sec=19.0,
            control_start_sec=14.0,
        )
    ]


@pytest.mark.parametrize(
    "segments",
    [
        None,
        [{}, {"start_sec": 1, "end_sec": 2}],
        [{"start_sec": 2, "end_sec": 1}, {"start_sec": 1, "end_sec": 2}],
    ],
)
def test_invalid_checkpoint_topology_fails_closed(segments: object) -> None:
    with pytest.raises(ValueError):
        seam_test._seam_windows(segments, frames=8, fps=24.0)
