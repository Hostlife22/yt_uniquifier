"""v1.1.0 Task 20: ``yt-uniq run --dry-run`` prints the plan summary
without spawning ffmpeg.

We use ``tiny_clip`` (a sub-second testsrc2 source) so the test runs
quickly while still going through probe + preflight + segment plan
+ filter_complex build — exactly the dry-run code path. The
``typer.testing.CliRunner`` invokes the typer app in-process, so we
don't depend on ``yt-uniq`` being on PATH.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import needs_ffmpeg
from yt_uniquifier.cli.app import app

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
def test_dry_run_prints_summary_and_skips_ffmpeg(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    profile = PROFILES_DIR / "soft.yaml"
    out = tmp_path / "out.mp4"
    work = tmp_path / "work"

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(tiny_clip),
            "--profile", str(profile),
            "--out", str(out),
            "--work-dir", str(work),
            "--no-qa",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, (
        f"run --dry-run exited {result.exit_code}\noutput:\n{result.output}"
    )

    out_text = result.output
    assert "dry-run summary" in out_text
    assert "encoder" in out_text
    assert "segments" in out_text
    assert "disk estimate" in out_text
    assert "eta" in out_text
    assert "filter_complex" in out_text
    # Sentinel — must NOT have produced the real output.
    assert not out.exists()


@needs_ffmpeg
@pytest.mark.integration
def test_profile_auto_picks_shipped_slug(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """v1.1.0 Task 21: ``--profile auto`` resolves to a shipped YAML."""
    out = tmp_path / "out.mp4"
    work = tmp_path / "work"

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(tiny_clip),
            "--profile", "auto",
            "--out", str(out),
            "--work-dir", str(work),
            "--no-qa",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, (
        f"run --profile auto --dry-run exited {result.exit_code}\n"
        f"output:\n{result.output}"
    )
    assert "profile auto:" in result.output
    # tiny_clip is 320x180 landscape — the recommender's default branch
    # should pick ``medium``.
    assert "medium" in result.output
