"""Integration test for `yt-uniq batch`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import needs_ffmpeg
from yt_uniquifier.cli.app import app

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
def test_batch_processes_two_files(tiny_clip: Path, tmp_path: Path,
                                    isolated_cache: Path) -> None:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(tiny_clip, in_dir / "a.mp4")
    shutil.copy(tiny_clip, in_dir / "b.mp4")

    out_dir = tmp_path / "out"

    res = CliRunner().invoke(
        app,
        [
            "batch", str(in_dir),
            "--profile", str(PROFILES_DIR / "soft.yaml"),
            "--out", str(out_dir),
            "--encoder", "libx264",
            "--work-dir", str(tmp_path / "work"),
            "--no-qa",
        ],
        catch_exceptions=False,
    )
    assert res.exit_code == 0, res.stdout
    assert (out_dir / "a.uniq.mp4").exists()
    assert (out_dir / "b.uniq.mp4").exists()


@needs_ffmpeg
@pytest.mark.integration
def test_batch_continue_on_error(tiny_clip: Path, tmp_path: Path,
                                  isolated_cache: Path) -> None:
    """A bad input shouldn't stop the run when --continue-on-error is set (default)."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(tiny_clip, in_dir / "good.mp4")
    # Plant a malformed mp4 (zero bytes).
    (in_dir / "bad.mp4").touch()

    out_dir = tmp_path / "out"
    res = CliRunner().invoke(
        app,
        [
            "batch", str(in_dir),
            "--profile", str(PROFILES_DIR / "soft.yaml"),
            "--out", str(out_dir),
            "--encoder", "libx264",
            "--work-dir", str(tmp_path / "work"),
            "--no-qa",
        ],
        catch_exceptions=False,
    )
    # exit code 1 because at least one failed, but the good one must be processed.
    assert res.exit_code == 1
    assert (out_dir / "good.uniq.mp4").exists()
