"""resolve_run_seed: fixed / per_file / per_run semantics."""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.models import (
    AudioStream,
    HDRInfo,
    Profile,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.seed_resolver import resolve_run_seed


def _src(path: Path) -> SourceMeta:
    path.touch()
    return SourceMeta(
        path=path, container="mp4", duration_sec=10, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720, fps=24,
                           duration_sec=10, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def test_fixed_uses_profile_seed(tmp_path: Path) -> None:
    src = _src(tmp_path / "x.mp4")
    p = Profile(name="t", seed=12345, seed_strategy="fixed")
    assert resolve_run_seed(p, src) == 12345


def test_fixed_with_none_seed_returns_zero(tmp_path: Path) -> None:
    src = _src(tmp_path / "x.mp4")
    p = Profile(name="t", seed=None, seed_strategy="fixed")
    assert resolve_run_seed(p, src) == 0


def test_per_file_is_deterministic(tmp_path: Path) -> None:
    src = _src(tmp_path / "a.mp4")
    p = Profile(name="t", seed_strategy="per_file")
    s1 = resolve_run_seed(p, src)
    s2 = resolve_run_seed(p, src)
    assert s1 == s2
    assert 0 <= s1 < 2**32


def test_per_file_differs_across_paths(tmp_path: Path) -> None:
    a = _src(tmp_path / "a.mp4")
    b = _src(tmp_path / "b.mp4")
    p = Profile(name="t", seed_strategy="per_file")
    assert resolve_run_seed(p, a) != resolve_run_seed(p, b)


def test_per_run_changes_each_call(tmp_path: Path) -> None:
    src = _src(tmp_path / "x.mp4")
    p = Profile(name="t", seed_strategy="per_run")
    seeds = {resolve_run_seed(p, src) for _ in range(8)}
    # Astronomically unlikely to collide.
    assert len(seeds) >= 7
    for s in seeds:
        assert 0 <= s < 2**32
