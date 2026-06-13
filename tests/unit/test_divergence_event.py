"""v0.7 R4 / F2 — backend contract for divergence_sample RunEvent.

Verifies:
  * RunEvent.kind = "divergence_sample" is accepted by the typed dataclass
  * `_maybe_emit_divergence` cadence: off skips entirely; light
    samples every 4th segment; full samples every segment
  * Sampler errors are swallowed (best-effort observability never
    crashes the encode)
  * RunOptions(sample_phash=...) Literal validation works
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from yt_uniquifier.core.orchestrator import RunOptions, _maybe_emit_divergence
from yt_uniquifier.core.runner import RunEvent


def test_run_event_accepts_divergence_sample_kind() -> None:
    ev = RunEvent(
        kind="divergence_sample",
        payload={
            "segment": 3,
            "phash_similarity": 0.87,
            "running_phash": 0.88,
            "frames_sampled": 2,
        },
    )
    assert ev.kind == "divergence_sample"
    assert ev.payload["segment"] == 3


def test_run_options_sample_phash_default_is_off() -> None:
    opts = RunOptions(work_dir=Path("/tmp/wd"), output=Path("/tmp/o.mp4"))
    assert opts.sample_phash == "off"


@pytest.mark.parametrize("mode", ["off", "light", "full"])
def test_run_options_sample_phash_modes_accepted(mode: str) -> None:
    opts = RunOptions(
        work_dir=Path("/tmp/wd"), output=Path("/tmp/o.mp4"),
        sample_phash=mode,  # type: ignore[arg-type]
    )
    assert opts.sample_phash == mode


# ---- _maybe_emit_divergence cadence ----------------------------------------

class _FakeSeg:
    def __init__(self, idx: int, start: float, end: float) -> None:
        self.idx = idx
        self.start_sec = start
        self.end_sec = end


class _FakeSource:
    def __init__(self, path: Path) -> None:
        self.path = path


class _FakePlan:
    def __init__(self, src: Path) -> None:
        self.plan_hash = "test-hash-xyz"
        self.source = _FakeSource(src)


def _collected_emit():
    events: list[RunEvent] = []

    def _emit(ev: RunEvent) -> None:
        events.append(ev)
    return events, _emit


def test_off_mode_emits_nothing(tmp_path: Path) -> None:
    events, emit = _collected_emit()
    opts = RunOptions(work_dir=tmp_path, output=tmp_path / "o.mp4")  # off
    seg = _FakeSeg(0, 0.0, 1.0)
    plan = _FakePlan(tmp_path / "in.mp4")

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        return_value=0.9,
    ):
        _maybe_emit_divergence(0, tmp_path / "seg.mkv", {0: seg}, opts, plan, emit)
    assert events == []


def test_light_mode_samples_every_4th_segment(tmp_path: Path) -> None:
    events, emit = _collected_emit()
    opts = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4",
        sample_phash="light",
    )
    plan = _FakePlan(tmp_path / "in.mp4")
    seg_by_idx = {i: _FakeSeg(i, float(i), float(i + 1)) for i in range(8)}

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        return_value=0.91,
    ):
        for idx in range(8):
            _maybe_emit_divergence(
                idx, tmp_path / f"seg{idx}.mkv",
                seg_by_idx, opts, plan, emit,
            )
    sampled_segs = [ev.payload["segment"] for ev in events]
    # Light mode = every 4th (0, 4) out of 0..7.
    assert sampled_segs == [0, 4]


def test_full_mode_samples_every_segment(tmp_path: Path) -> None:
    events, emit = _collected_emit()
    opts = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4",
        sample_phash="full",
    )
    plan = _FakePlan(tmp_path / "in.mp4")
    seg_by_idx = {i: _FakeSeg(i, float(i), float(i + 1)) for i in range(4)}

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        return_value=0.8,
    ):
        for idx in range(4):
            _maybe_emit_divergence(
                idx, tmp_path / f"seg{idx}.mkv",
                seg_by_idx, opts, plan, emit,
            )
    sampled_segs = [ev.payload["segment"] for ev in events]
    assert sampled_segs == [0, 1, 2, 3]
    # Full mode requests 4 frames per pair.
    assert events[0].payload["frames_sampled"] == 4


def test_sampler_failure_does_not_raise(tmp_path: Path) -> None:
    """Exceptions inside compare_range_pair must not propagate."""
    events, emit = _collected_emit()
    opts = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4",
        sample_phash="full",
    )
    plan = _FakePlan(tmp_path / "in.mp4")
    seg_by_idx = {0: _FakeSeg(0, 0.0, 1.0)}

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        side_effect=RuntimeError("ffmpeg blew up"),
    ):
        # Must not raise. Should emit a `log` event carrying the warning.
        _maybe_emit_divergence(
            0, tmp_path / "seg.mkv", seg_by_idx, opts, plan, emit,
        )
    assert any(ev.kind == "log" for ev in events)
    assert not any(ev.kind == "divergence_sample" for ev in events)


def test_running_ema_smooths_samples(tmp_path: Path) -> None:
    """`running_phash` must average toward incoming values, not snap."""
    events, emit = _collected_emit()
    opts = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4",
        sample_phash="full",
    )
    plan = _FakePlan(tmp_path / "in.mp4")
    # Unique hash so the per-plan EMA cache doesn't carry over.
    plan.plan_hash = "ema-test-hash-1"
    seg_by_idx = {i: _FakeSeg(i, float(i), float(i + 1)) for i in range(3)}
    similarities = [1.0, 0.6, 0.6]

    def _stub(*_a, **_kw):
        return similarities.pop(0)

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        side_effect=_stub,
    ):
        for idx in range(3):
            _maybe_emit_divergence(
                idx, tmp_path / f"seg{idx}.mkv",
                seg_by_idx, opts, plan, emit,
            )
    runs = [ev.payload["running_phash"] for ev in events]
    # First sample seeds the EMA; second pulls toward 0.6 but doesn't snap.
    assert runs[0] == 1.0
    assert 0.6 < runs[1] < 1.0
    # Third sample pulls EMA further down.
    assert runs[2] < runs[1]


def test_zero_span_segment_skipped(tmp_path: Path) -> None:
    """end_sec == start_sec should be treated as degenerate, not emit."""
    events, emit = _collected_emit()
    opts = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4",
        sample_phash="full",
    )
    plan = _FakePlan(tmp_path / "in.mp4")
    seg_by_idx = {0: _FakeSeg(0, 5.0, 5.0)}  # span = 0

    with patch(
        "yt_uniquifier.core.qa.phash.compare_range_pair",
        return_value=0.9,
    ):
        _maybe_emit_divergence(
            0, tmp_path / "seg.mkv", seg_by_idx, opts, plan, emit,
        )
    assert events == []
