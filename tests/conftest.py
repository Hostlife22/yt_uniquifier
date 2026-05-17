"""Shared fixtures.

Tiny clips are generated in-process via ffmpeg testsrc2 / sine so the repo
doesn't carry binary blobs and CI runs without pre-staged files.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


needs_ffmpeg = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH",
)


@pytest.fixture(scope="session")
def tiny_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a 2-second 320x180 24fps mp4 with sine audio. Session-scoped."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("clips") / "tiny.mp4"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect encoder cache to a temp path for the test's lifetime."""
    cache = tmp_path / "encoders.json"
    monkeypatch.setattr("yt_uniquifier.core.encoder.CACHE_PATH", cache)
    return cache
