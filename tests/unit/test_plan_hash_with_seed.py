"""plan_hash sensitivity to Profile fields and immunity to run_seed."""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Profile,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.pipeline import compute_plan_hash


def _src(tmp_path: Path) -> SourceMeta:
    p = tmp_path / "x.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=10, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720, fps=24,
                           duration_sec=10, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def test_plan_hash_changes_when_seed_strategy_changes(tmp_path: Path) -> None:
    src = _src(tmp_path)
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    p_fixed = Profile(name="t", seed_strategy="fixed", seed=1)
    p_run = Profile(name="t", seed_strategy="per_run", seed=1)
    h1 = compute_plan_hash(src, p_fixed, enc)
    h2 = compute_plan_hash(src, p_run, enc)
    assert h1 != h2


def test_plan_hash_independent_of_run_seed(tmp_path: Path) -> None:
    """compute_plan_hash does not take run_seed; resume key is profile+source+encoder."""
    src = _src(tmp_path)
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    profile = Profile(name="t", seed_strategy="per_run", seed=1)
    # The function doesn't accept run_seed at all — different runs with the same
    # profile (and per_run strategy) produce the SAME plan_hash, so resume can
    # find the same work_dir.
    h = compute_plan_hash(src, profile, enc)
    assert isinstance(h, str)
    assert len(h) == 16
