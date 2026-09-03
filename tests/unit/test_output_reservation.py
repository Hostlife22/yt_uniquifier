"""Cross-process final-output reservation regressions."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from yt_uniquifier.core.output_reservation import (
    OutputReservation,
    OutputReservationConflict,
    OutputReservationError,
)


def test_conflict_release_and_reacquire(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    first = OutputReservation.acquire(output, "run-a")

    with pytest.raises(OutputReservationConflict, match="already reserved"):
        OutputReservation.acquire(output, "run-b")

    first.release()
    second = OutputReservation.acquire(output, "run-b")
    second.release()


def test_dead_local_owner_is_reclaimed(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    lock_dir = tmp_path / ".yt_uniquifier-reservations"
    lock_dir.mkdir()
    lock = lock_dir / "result.mp4.lock"
    lock.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "dead-run",
        "pid": 2_000_000_001,
        "hostname": socket.gethostname(),
    }), encoding="utf-8")

    reservation = OutputReservation.acquire(output, "replacement-run")

    owner = json.loads(lock.read_text(encoding="utf-8"))
    assert owner["run_id"] == "replacement-run"
    reservation.release()


def test_foreign_host_owner_is_not_reclaimed(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    lock_dir = tmp_path / ".yt_uniquifier-reservations"
    lock_dir.mkdir()
    lock = lock_dir / "result.mp4.lock"
    lock.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "remote-run",
        "pid": 2_000_000_001,
        "hostname": "another-host",
    }), encoding="utf-8")

    with pytest.raises(OutputReservationConflict, match="already reserved"):
        OutputReservation.acquire(output, "local-run")

    assert json.loads(lock.read_text(encoding="utf-8"))["run_id"] == "remote-run"


def test_persistence_failure_removes_partial_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.mp4"

    def fail_fsync(fd: int) -> None:
        del fd
        raise OSError("injected fsync failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fsync", fail_fsync)
        with pytest.raises(
            OutputReservationError,
            match="could not persist output reservation",
        ) as exc_info:
            OutputReservation.acquire(output, "failed-run")
        assert isinstance(exc_info.value.__cause__, OSError)

    lock = tmp_path / ".yt_uniquifier-reservations" / "result.mp4.lock"
    assert not lock.exists()
    replacement = OutputReservation.acquire(output, "replacement-run")
    replacement.release()


def test_release_does_not_remove_a_different_owner(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    reservation = OutputReservation.acquire(output, "run-a")
    reservation.lock_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-b",
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }), encoding="utf-8")

    reservation.release()

    assert reservation.lock_path.exists()


def test_separate_process_observes_live_reservation(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    reservation = OutputReservation.acquire(output, "parent-run")
    snippet = textwrap.dedent(f"""
        from pathlib import Path
        from yt_uniquifier.core.output_reservation import (
            OutputReservation, OutputReservationConflict,
        )

        try:
            OutputReservation.acquire(Path({str(output)!r}), "child-run")
        except OutputReservationConflict:
            raise SystemExit(42)
        raise SystemExit(0)
    """)
    try:
        child = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        reservation.release()

    assert child.returncode == 42, child.stderr


def test_reservation_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    reservation_dir = tmp_path / ".yt_uniquifier-reservations"
    try:
        reservation_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable for this test account")

    with pytest.raises(OutputReservationError, match="not a directory"):
        OutputReservation.acquire(tmp_path / "result.mp4", "run-a")

    assert not (outside / "result.mp4.lock").exists()
