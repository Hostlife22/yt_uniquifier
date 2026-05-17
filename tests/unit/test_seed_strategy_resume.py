"""seed_strategy interaction with checkpoint resume."""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    VideoStream,
)


def _plan(tmp_path: Path, run_seed: int = 0, plan_hash: str = "h" * 16) -> Plan:
    src = tmp_path / "source.mp4"
    src.touch()
    source = SourceMeta(
        path=src, container="mp4", duration_sec=60, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720, fps=24,
                           duration_sec=60, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    return Plan(
        source=source,
        profile=Profile(name="t", seed_strategy="per_run"),
        encoder=EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        plan_hash=plan_hash,
        run_seed=run_seed,
    )


def _segments() -> list[Segment]:
    return [Segment(idx=0, start_sec=0, end_sec=30),
            Segment(idx=1, start_sec=30, end_sec=60)]


def test_first_run_writes_run_seed_to_state(tmp_path: Path) -> None:
    p = _plan(tmp_path, run_seed=42)
    store = CheckpointStore(tmp_path / "work", p)
    store.init_or_resume(_segments())
    assert store.stored_run_seed() == 42


def test_resume_returns_stored_seed_even_if_plan_has_different_one(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    # First run with seed 42.
    p1 = _plan(tmp_path, run_seed=42)
    s1 = CheckpointStore(work, p1)
    s1.init_or_resume(_segments())

    # Second invocation: same plan_hash but a freshly-rolled run_seed.
    p2 = _plan(tmp_path, run_seed=999)
    s2 = CheckpointStore(work, p2)
    s2.init_or_resume(_segments())
    # State on disk still carries seed=42; caller can override plan.run_seed.
    assert s2.stored_run_seed() == 42


def test_different_plan_hash_invalidates_seed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    p1 = _plan(tmp_path, run_seed=42, plan_hash="hash_a" * 3)
    CheckpointStore(work, p1).init_or_resume(_segments())

    # Plan changed (different hash) → state archived, new seed used.
    p2 = _plan(tmp_path, run_seed=99, plan_hash="hash_b" * 3)
    s2 = CheckpointStore(work, p2)
    s2.init_or_resume(_segments())
    assert s2.stored_run_seed() == 99
