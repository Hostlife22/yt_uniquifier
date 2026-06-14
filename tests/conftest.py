"""Shared fixtures.

Tiny clips are generated in-process via ffmpeg testsrc2 / sine so the repo
doesn't carry binary blobs and CI runs without pre-staged files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# v0.5.0 — GUI tests need a Qt platform; offscreen works headless on CI.
# Set before any PyQt6 import happens (collection time, not just test time).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
    # 30 s cap so a wedged ffmpeg fixture can't hang the whole suite —
    # a 2 s testsrc2 clip generates in <1 s on every supported runner.
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    return out


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect encoder cache to a temp path for the test's lifetime."""
    cache = tmp_path / "encoders.json"
    monkeypatch.setattr("yt_uniquifier.core.encoder.CACHE_PATH", cache)
    return cache


@pytest.fixture(autouse=True)
def _isolated_gui_state(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect GUI ``CONFIG_DIR`` / ``STATE_PATH`` / ``HISTORY_PATH`` to a
    per-test temp directory so a test can never read or pollute the real
    ``~/Library/Preferences/yt-uniquifier`` / ``%APPDATA%`` location.

    v0.7.0 R8: the v0.5.x layout used ``~/.config/yt_uniquifier``, which
    was already shared. v0.5.5 + E3 moved this to ``QStandardPaths``,
    which made every CI host's home a write target. Without this
    fixture two failure modes are possible:

    * A previous run's corrupt ``state.json`` lingers and the next
      ``AppState()`` call quietly archives it — slow on big files.
    * Multiple parametrized tests racing on the same file under
      a parallel pytest runner (xdist) corrupt each other's state.

    Imported lazily so unit tests that never touch ``gui.state`` don't
    pay for the import.
    """
    try:
        from yt_uniquifier.gui import state as state_mod
    except ImportError:
        return  # GUI extras not installed — nothing to isolate

    cfg = tmp_path_factory.mktemp("gui_cfg")
    monkeypatch.setattr(state_mod, "CONFIG_DIR", cfg)
    monkeypatch.setattr(state_mod, "STATE_PATH", cfg / "state.json")
    monkeypatch.setattr(state_mod, "HISTORY_PATH", cfg / "history.json")
