"""parallel_safe + process_video_segments_parallel dispatch."""

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


def _plan(encoder_name: str, vendor: str) -> Plan:
    src = SourceMeta(
        path=Path("/tmp/fake.mp4"), container="mp4", duration_sec=10,
        size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720,
                           fps=24, duration_sec=10, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[],
    )
    enc = EncoderCandidate(name=encoder_name, vendor=vendor,  # type: ignore[arg-type]
                            codec="h264", works=True)
    return Plan(source=src, profile=Profile(name="t"), encoder=enc,
                plan_hash="x", run_seed=0)


def test_parallel_safe_libx264() -> None:
    assert seg_mod.parallel_safe(_plan("libx264", "x264")) is True


def test_parallel_safe_libx265() -> None:
    assert seg_mod.parallel_safe(_plan("libx265", "x265")) is True


def test_parallel_unsafe_nvenc() -> None:
    assert seg_mod.parallel_safe(_plan("h264_nvenc", "nvenc")) is False


def test_parallel_unsafe_videotoolbox() -> None:
    assert seg_mod.parallel_safe(_plan("h264_videotoolbox", "videotoolbox")) is False


def test_workers_one_dispatches_sequentially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_process(seg: Segment, plan: Plan, work_dir: Path, **_kw: object) -> tuple[Path, Path]:
        calls.append(seg.idx)
        src = work_dir / f"s{seg.idx}.mkv"
        out = work_dir / f"o{seg.idx}.mkv"
        src.touch()
        out.touch()
        return src, out

    monkeypatch.setattr(seg_mod, "process_video_segment", fake_process)
    plan = _plan("libx264", "x264")
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(3)]
    results = seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=1,
    )
    assert calls == [0, 1, 2]
    assert sorted(r[0] for r in results) == [0, 1, 2]


def test_workers_many_runs_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All segments dispatched (order may differ); each produces (src,out)."""
    def fake_process(seg: Segment, plan: Plan, work_dir: Path, **_kw: object) -> tuple[Path, Path]:
        src = work_dir / f"s{seg.idx}.mkv"
        out = work_dir / f"o{seg.idx}.mkv"
        src.touch()
        out.touch()
        return src, out

    monkeypatch.setattr(seg_mod, "process_video_segment", fake_process)
    plan = _plan("libx264", "x264")
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(4)]
    results = seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=4,
    )
    assert sorted(r[0] for r in results) == [0, 1, 2, 3]


def test_gpu_encoder_falls_back_to_sequential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_process(seg: Segment, plan: Plan, work_dir: Path, **_kw: object) -> tuple[Path, Path]:
        calls.append(seg.idx)
        src = work_dir / f"s{seg.idx}.mkv"
        out = work_dir / f"o{seg.idx}.mkv"
        src.touch()
        out.touch()
        return src, out

    monkeypatch.setattr(seg_mod, "process_video_segment", fake_process)
    plan = _plan("h264_nvenc", "nvenc")
    segs = [Segment(idx=i, start_sec=i * 5, end_sec=(i + 1) * 5) for i in range(3)]
    seg_mod.process_video_segments_parallel(
        segs, plan, tmp_path, workers=4,   # ignored
    )
    assert calls == [0, 1, 2]  # sequential despite workers=4
