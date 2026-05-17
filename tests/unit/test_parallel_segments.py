"""parallel_safe + process_video_segments_parallel dispatch + cap (v0.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core import segmenter as seg_mod
from yt_uniquifier.core.models import (
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.runner import RunEvent


def _plan(encoder_name: str, vendor: str, *, max_parallel: int = 1) -> Plan:
    src = SourceMeta(
        path=Path("/tmp/fake.mp4"), container="mp4", duration_sec=10,
        size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720,
                           fps=24, duration_sec=10, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[],
    )
    enc = EncoderCandidate(
        name=encoder_name, vendor=vendor,  # type: ignore[arg-type]
        codec="h264", works=True, max_parallel=max_parallel,
    )
    return Plan(source=src, profile=Profile(name="t"), encoder=enc,
                plan_hash="x", run_seed=0)


def _fake_process(
    seg: Segment, plan: Plan, work_dir: Path, **_kw: object,
) -> tuple[Path, Path]:
    src = work_dir / f"s{seg.idx}.mkv"
    out = work_dir / f"o{seg.idx}.mkv"
    src.touch()
    out.touch()
    return src, out


# ---- parallel_safe ---------------------------------------------------------

def test_parallel_safe_returns_max_parallel() -> None:
    """v0.3: parallel_safe returns the encoder's per-machine cap (int)."""
    assert seg_mod.parallel_safe(_plan("libx264", "x264", max_parallel=6)) == 6
    assert seg_mod.parallel_safe(_plan("h264_nvenc", "nvenc", max_parallel=3)) == 3


def test_parallel_safe_floor_at_one() -> None:
    # max_parallel=1 is the lowest valid value; ensure we never return < 1.
    assert seg_mod.parallel_safe(_plan("libx264", "x264", max_parallel=1)) == 1


# ---- process_video_segments_parallel dispatch -----------------------------

def test_workers_one_dispatches_sequentially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _spy(seg, plan, work_dir, **kw):
        calls.append(seg.idx)
        return _fake_process(seg, plan, work_dir, **kw)

    monkeypatch.setattr(seg_mod, "process_video_segment", _spy)
    plan = _plan("libx264", "x264", max_parallel=4)
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(3)]
    results = seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=1,
    )
    assert calls == [0, 1, 2]
    assert sorted(r[0] for r in results) == [0, 1, 2]


def test_workers_within_cap_runs_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seg_mod, "process_video_segment", _fake_process)
    plan = _plan("libx264", "x264", max_parallel=4)
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(4)]
    results = seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=4,
    )
    assert sorted(r[0] for r in results) == [0, 1, 2, 3]


def test_workers_above_cap_get_downgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting more workers than the encoder supports emits a log + downgrades."""
    monkeypatch.setattr(seg_mod, "process_video_segment", _fake_process)

    events: list[RunEvent] = []
    plan = _plan("h264_nvenc", "nvenc", max_parallel=3)  # GPU cap = 3
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(5)]

    seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=8,                 # requested 8
        on_event=events.append,
    )
    msgs = [e.payload.get("message") for e in events if e.payload.get("phase") == "workers"]
    assert any("8 → 3" in str(m) for m in msgs)


def test_cap_one_forces_sequential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_parallel=1 on the encoder forces sequential regardless of workers."""
    calls: list[int] = []

    def _spy(seg, plan, work_dir, **kw):
        calls.append(seg.idx)
        return _fake_process(seg, plan, work_dir, **kw)

    monkeypatch.setattr(seg_mod, "process_video_segment", _spy)
    plan = _plan("h264_videotoolbox", "videotoolbox", max_parallel=1)
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(3)]
    seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=4,
    )
    assert calls == [0, 1, 2]
