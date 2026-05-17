"""Integration: a single in-process worker drains a 2-file queue."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import needs_ffmpeg
from yt_uniquifier.cli.app import app
from yt_uniquifier.core.queue.leasing import FileQueue, init_queue

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
def test_queue_cli_roundtrip(tiny_clip: Path, tmp_path: Path) -> None:
    """init → add → status → reset basic CLI sequence."""
    qdir = tmp_path / "q"
    runner = CliRunner()

    r = runner.invoke(app, ["queue", "init", str(qdir)])
    assert r.exit_code == 0, r.stdout
    assert (qdir / "pending").is_dir()

    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    shutil.copy(tiny_clip, a)
    shutil.copy(tiny_clip, b)
    r = runner.invoke(app, ["queue", "add", str(qdir), str(a), str(b)])
    assert r.exit_code == 0, r.stdout

    r = runner.invoke(app, ["queue", "status", str(qdir), "--json"])
    assert r.exit_code == 0
    import json
    s = json.loads(r.stdout)
    assert s["pending"] == 2
    assert s["in_progress"] == 0
    assert s["done"] == 0


@needs_ffmpeg
@pytest.mark.integration
def test_worker_drains_two_files(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """Worker leases and processes both files in stop-after-empty mode."""
    qdir = tmp_path / "q"
    init_queue(qdir)
    q = FileQueue(qdir, host="tester")

    for n in ("a.mp4", "b.mp4"):
        clip_copy = tmp_path / n
        shutil.copy(tiny_clip, clip_copy)
        q.add(clip_copy)

    out_dir = tmp_path / "out"
    runner = CliRunner()
    r = runner.invoke(app, [
        "worker", str(qdir),
        "--profile", str(PROFILES_DIR / "soft.yaml"),
        "--out-dir", str(out_dir),
        "--encoder", "libx264",
        "--workers", "1",
        "--work-dir", str(tmp_path / "work"),
        "--stop-after-empty",
        "--poll-sec", "0.1",
        "--heartbeat-sec", "0.5",
    ])
    assert r.exit_code == 0, r.stdout

    s = q.stats()
    assert s["pending"] == 0
    assert s["done"] == 2
    assert (out_dir / "a.uniq.mp4").exists()
    assert (out_dir / "b.uniq.mp4").exists()


@needs_ffmpeg
@pytest.mark.integration
def test_worker_handles_bad_input(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """A malformed input goes to failed/<host>/, not done/."""
    qdir = tmp_path / "q"
    init_queue(qdir)
    q = FileQueue(qdir)

    ok = tmp_path / "ok.mp4"
    shutil.copy(tiny_clip, ok)
    q.add(ok)

    bad = tmp_path / "bad.mp4"
    bad.touch()  # zero-byte file — probe will reject
    q.add(bad)

    out_dir = tmp_path / "out"
    runner = CliRunner()
    r = runner.invoke(app, [
        "worker", str(qdir),
        "--profile", str(PROFILES_DIR / "soft.yaml"),
        "--out-dir", str(out_dir),
        "--encoder", "libx264",
        "--work-dir", str(tmp_path / "work"),
        "--stop-after-empty",
        "--poll-sec", "0.1",
    ])
    # Worker exits 0 even with per-file failures.
    assert r.exit_code == 0, r.stdout

    s = q.stats()
    assert s["done"] == 1
    assert s["failed"] == 1
