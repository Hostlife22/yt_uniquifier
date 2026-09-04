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
    RunAdmission,
    RunAdmissionError,
    RunAdmissionFull,
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


def test_shared_admission_enforces_capacity_and_reuses_released_slot(
    tmp_path: Path,
) -> None:
    first = RunAdmission.acquire(tmp_path, "run-a", 2)
    second = RunAdmission.acquire(tmp_path, "run-b", 2)

    with pytest.raises(RunAdmissionFull, match="capacity of 2"):
        RunAdmission.acquire(tmp_path, "run-c", 2)

    first.release()
    replacement = RunAdmission.acquire(tmp_path, "run-c", 2)
    assert replacement.lock_path.name == first.lock_path.name
    replacement.release()
    second.release()


def test_shared_admission_retries_when_contended_slot_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent release after O_EXCL contention is not pool corruption."""
    admission_dir = tmp_path / ".yt_uniquifier-admission"
    admission_dir.mkdir()
    (admission_dir / "capacity.json").write_text(json.dumps({
        "schema_version": 1,
        "capacity": 1,
    }), encoding="utf-8")

    real_open = os.open
    collision_injected = False

    def open_after_concurrent_release(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        nonlocal collision_injected
        if Path(path).name == "slot-0000.lock" and not collision_injected:
            collision_injected = True
            raise FileExistsError(path)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", open_after_concurrent_release)
    admission = RunAdmission.acquire(tmp_path, "run-after-release", 1)

    assert collision_injected
    assert admission.lock_path.is_file()
    admission.release()


def test_shared_admission_rejects_capacity_mismatch(tmp_path: Path) -> None:
    admission = RunAdmission.acquire(tmp_path, "run-a", 2)
    try:
        with pytest.raises(RunAdmissionError, match="capacity does not match"):
            RunAdmission.acquire(tmp_path, "run-b", 3)
    finally:
        admission.release()


def test_shared_admission_reclaims_dead_local_owner(tmp_path: Path) -> None:
    admission_dir = tmp_path / ".yt_uniquifier-admission"
    admission_dir.mkdir()
    (admission_dir / "capacity.json").write_text(json.dumps({
        "schema_version": 1,
        "capacity": 1,
    }), encoding="utf-8")
    slot = admission_dir / "slot-0000.lock"
    slot.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "dead-run",
        "pid": 2_000_000_001,
        "hostname": socket.gethostname(),
    }), encoding="utf-8")

    replacement = RunAdmission.acquire(tmp_path, "replacement", 1)
    owner = json.loads(slot.read_text(encoding="utf-8"))
    assert owner["run_id"] == "replacement"
    replacement.release()


def test_shared_admission_foreign_owner_fails_closed(tmp_path: Path) -> None:
    admission_dir = tmp_path / ".yt_uniquifier-admission"
    admission_dir.mkdir()
    (admission_dir / "capacity.json").write_text(json.dumps({
        "schema_version": 1,
        "capacity": 1,
    }), encoding="utf-8")
    slot = admission_dir / "slot-0000.lock"
    slot.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "remote-run",
        "pid": 2_000_000_001,
        "hostname": "another-host",
    }), encoding="utf-8")

    with pytest.raises(RunAdmissionFull, match="fully occupied"):
        RunAdmission.acquire(tmp_path, "local-run", 1)

    assert json.loads(slot.read_text(encoding="utf-8"))["run_id"] == "remote-run"


def test_separate_process_observes_shared_admission_limit(tmp_path: Path) -> None:
    admission = RunAdmission.acquire(tmp_path, "parent-run", 1)
    snippet = textwrap.dedent(f"""
        from pathlib import Path
        from yt_uniquifier.core.output_reservation import RunAdmission, RunAdmissionFull

        try:
            RunAdmission.acquire(Path({str(tmp_path)!r}), "child-run", 1)
        except RunAdmissionFull:
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
        admission.release()

    assert child.returncode == 42, child.stderr


def test_admission_release_does_not_remove_a_different_owner(tmp_path: Path) -> None:
    admission = RunAdmission.acquire(tmp_path, "run-a", 1)
    admission.lock_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-b",
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }), encoding="utf-8")

    admission.release()

    assert admission.lock_path.exists()


def test_admission_persistence_failure_removes_partial_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    calls = 0

    def fail_slot_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected slot fsync failure")
        real_fsync(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fsync", fail_slot_fsync)
        with pytest.raises(
            RunAdmissionError,
            match="could not persist shared admission slot",
        ) as exc_info:
            RunAdmission.acquire(tmp_path, "failed-run", 1)
        assert isinstance(exc_info.value.__cause__, OSError)

    slot = tmp_path / ".yt_uniquifier-admission" / "slot-0000.lock"
    assert not slot.exists()
    replacement = RunAdmission.acquire(tmp_path, "replacement-run", 1)
    replacement.release()


def test_admission_malformed_owner_fails_closed(tmp_path: Path) -> None:
    admission_dir = tmp_path / ".yt_uniquifier-admission"
    admission_dir.mkdir()
    (admission_dir / "capacity.json").write_text(json.dumps({
        "schema_version": 1,
        "capacity": 1,
    }), encoding="utf-8")
    slot = admission_dir / "slot-0000.lock"
    slot.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(RunAdmissionFull, match="fully occupied"):
        RunAdmission.acquire(tmp_path, "local-run", 1)

    assert slot.read_text(encoding="utf-8") == "not-json\n"


def test_admission_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    admission_dir = tmp_path / ".yt_uniquifier-admission"
    try:
        admission_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable for this test account")

    with pytest.raises(RunAdmissionError, match="not a directory"):
        RunAdmission.acquire(tmp_path, "run-a", 1)

    assert not (outside / "capacity.json").exists()
