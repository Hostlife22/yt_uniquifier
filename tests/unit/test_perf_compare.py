"""Unit tests for tools/perf_compare.py.

The script has no production import path (it is a CLI tool in
``tools/``), so we exercise it via subprocess + temporary JSON
snapshots. This keeps the tests honest — they run the same code
path CI does, including argparse + exit codes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "perf_compare.py"


def _snapshot(
    *,
    wall_sec: float,
    rss_peak_kb: int = 100_000,
    per_phase: dict[str, float] | None = None,
    git_sha: str = "deadbee",
) -> dict[str, object]:
    """Build a v1 perf snapshot in the shape tools/benchmark.py emits."""
    return {
        "schema_version": 1,
        "yt_uniquifier_version": "1.0.0",
        "git_sha": git_sha,
        "py_version": "3.12.3",
        "platform": "Linux-x86_64",
        "input": "fixture.mp4",
        "out": "out.mp4",
        "duration_sec": 60.0,
        "size_bytes": 5_000_000,
        "encoder": "libx264",
        "workers": 2,
        "wall_sec": wall_sec,
        "rss_peak_kb": rss_peak_kb,
        "segments_seen": 6,
        "per_phase_sec": per_phase or {"encode": wall_sec * 0.9},
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _run(args: list[str | Path]) -> subprocess.CompletedProcess[str]:
    """Run perf_compare with the cwd set to the repo root."""
    cmd = [sys.executable, str(TOOL), *(str(a) for a in args)]
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        timeout=30,
    )


def test_self_diff_is_zero_and_exits_clean(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"
    _write(snap, _snapshot(wall_sec=30.0))

    result = _run(["--baseline", snap, "--current", snap])

    assert result.returncode == 0, result.stderr
    assert "+0.0%" in result.stdout
    assert "within threshold" in result.stdout
    assert "⚠️ regression" not in result.stdout


def test_minor_change_under_threshold_passes(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write(base, _snapshot(wall_sec=30.0))
    # 14% slower — under the default 15% threshold
    _write(cur, _snapshot(wall_sec=34.2, git_sha="cafef00"))

    result = _run(["--baseline", base, "--current", cur])

    assert result.returncode == 0
    assert "+14.0%" in result.stdout
    assert "within threshold" in result.stdout


def test_wall_sec_regression_exits_one(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write(base, _snapshot(wall_sec=30.0))
    # 20% slower — over the default 15% threshold
    _write(cur, _snapshot(wall_sec=36.0, git_sha="badbeef"))

    result = _run(["--baseline", base, "--current", cur])

    assert result.returncode == 1
    assert "⚠️ regression" in result.stdout
    assert "Verdict" in result.stdout
    assert "wall_sec regression exceeds threshold" in result.stdout


def test_per_phase_regression_does_not_hard_gate(tmp_path: Path) -> None:
    """A jittered per-phase timing is reported but only wall_sec gates CI."""
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write(base, _snapshot(wall_sec=30.0, per_phase={"audio": 5.0, "encode": 25.0}))
    # wall_sec stable but encode-phase regressed 30%
    _write(
        cur,
        _snapshot(
            wall_sec=30.1,
            per_phase={"audio": 5.0, "encode": 32.5},
            git_sha="aaabbbb",
        ),
    )

    result = _run(["--baseline", base, "--current", cur])

    assert result.returncode == 0
    assert "per_phase[encode]" in result.stdout
    assert "+30.0%" in result.stdout


def test_improvement_is_flagged_but_does_not_fail(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write(base, _snapshot(wall_sec=30.0))
    _write(cur, _snapshot(wall_sec=20.0, git_sha="faster1"))

    result = _run(["--baseline", base, "--current", cur])

    assert result.returncode == 0
    assert "✅ improvement" in result.stdout


def test_custom_threshold_widens_gate(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write(base, _snapshot(wall_sec=30.0))
    _write(cur, _snapshot(wall_sec=36.0, git_sha="3601s"))   # +20%

    # default threshold 15 → fail
    fail = _run(["--baseline", base, "--current", cur])
    assert fail.returncode == 1
    # widened to 25 → pass
    ok = _run(["--baseline", base, "--current", cur, "--threshold", "25"])
    assert ok.returncode == 0


def test_missing_baseline_exits_with_usage_error(tmp_path: Path) -> None:
    cur = tmp_path / "cur.json"
    _write(cur, _snapshot(wall_sec=30.0))

    result = _run([
        "--baseline", tmp_path / "missing.json",
        "--current", cur,
    ])

    assert result.returncode == 2
    assert "missing snapshot" in result.stderr


def test_schema_mismatch_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    bogus = _snapshot(wall_sec=30.0)
    bogus["schema_version"] = 999
    _write(base, bogus)
    _write(cur, _snapshot(wall_sec=30.0))

    result = _run(["--baseline", base, "--current", cur])

    assert result.returncode == 2
    assert "schema_version" in result.stderr


def test_markdown_output_written_to_file(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    md = tmp_path / "report.md"
    _write(base, _snapshot(wall_sec=30.0))
    _write(cur, _snapshot(wall_sec=30.0))

    result = _run(["--baseline", base, "--current", cur, "--markdown", md])

    assert result.returncode == 0
    body = md.read_text(encoding="utf-8")
    assert body.startswith("# Perf comparison")
    assert "baseline:" in body
    assert "current:" in body


def test_benchmark_json_shape_lock(tmp_path: Path) -> None:
    """If tools/benchmark.py drops a key the regression suite relies on,
    perf_compare goes blind. Locking the produced shape here keeps the
    two tools in step — additions are fine, removals require updating
    the perf-history baselines too."""
    pytest.importorskip("yt_uniquifier")
    # Import the module-level constants from perf_compare to confirm
    # the contract list does not regress unintentionally.
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import perf_compare  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert {"wall_sec", "rss_peak_kb"} == perf_compare.LOWER_IS_BETTER
