"""CliRunner smoke coverage for every top-level command.

Previously only ``version`` and ``--help`` were unit-tested; the other
eight entry points were exercised only through full integration tests.
A typo in the typer wiring (missing required argument, wrong --help
text, broken subcommand registration) would only surface in integration
runs that require real ffmpeg. These tests catch wiring regressions in
<1 s with zero external dependencies.

We assert on ``--help`` for each command instead of running it for
real, so the tests do not need ffmpeg, fpcalc, a profile, a video, or
disk write access.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from yt_uniquifier.cli.app import app


@pytest.mark.parametrize(
    "command",
    [
        "probe",
        "run",
        "preflight",
        "qa",
        "batch",
        "calibrate",
        "worker",
    ],
)
def test_subcommand_help_is_wired(command: str) -> None:
    """Every command resolves and prints its own help block.

    A typer registration regression (forgotten ``app.command(...)``,
    typo in the entry function name, missing import) shows up as an
    exit code != 0 here.
    """
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, (
        f"`yt-uniq {command} --help` failed:\n{result.output}"
    )
    assert result.output, f"empty help output for {command}"


@pytest.mark.parametrize(
    "group",
    [
        "corpus",
        "queue",
    ],
)
def test_subgroup_help_is_wired(group: str) -> None:
    """Subcommand groups (``corpus``, ``queue``) resolve from the root app."""
    result = CliRunner().invoke(app, [group, "--help"])
    assert result.exit_code == 0, (
        f"`yt-uniq {group} --help` failed:\n{result.output}"
    )


def test_run_missing_input_exits_nonzero() -> None:
    """``run`` without its required INPUT argument must fail with a
    typer-style usage error, not a Python traceback."""
    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_probe_missing_path_handled() -> None:
    """``probe`` without a path → typer usage error, no traceback.

    Regression: an unhandled exception was raised when both
    ``--encoders`` and positional path were absent.
    """
    result = CliRunner().invoke(app, ["probe"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_batch_no_matching_inputs_exits_2(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``batch`` returns exit code 2 when the glob matches nothing.

    Distributed harnesses key on this distinct code to differentiate
    a no-op invocation from a real processing failure (exit 1).
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    profile = tmp_path / "p.yaml"
    profile.write_text("name: t\ntransforms: []\n", encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "batch", str(empty),
            "--profile", str(profile),
            "--out", str(out),
            "--pattern", "*.mp4",
        ],
    )
    assert result.exit_code == 2, (
        f"expected exit 2 for empty match; got {result.exit_code}: {result.output}"
    )
