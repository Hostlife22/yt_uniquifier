"""Cross-process encoder/disk resource-budget regressions."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from yt_uniquifier.core import resource_budget as budget
from yt_uniquifier.core.models import HDRInfo, SourceMeta, VideoStream


def _source(tmp_path: Path, *, duration: float = 10.0, bitrate: int = 8_000_000) -> SourceMeta:
    return SourceMeta(
        path=tmp_path / "source.mp4",
        container="mp4",
        duration_sec=duration,
        size_bytes=1,
        video=[VideoStream(
            index=0,
            codec="h264",
            width=1920,
            height=1080,
            fps=30.0,
            duration_sec=duration,
            pix_fmt="yuv420p",
            bit_rate=bitrate,
            color=HDRInfo(is_hdr=False),
        )],
        audio=[],
    )


def _fixed_free(monkeypatch: pytest.MonkeyPatch, free: int) -> None:
    monkeypatch.setattr(
        budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=free * 2, used=free, free=free),
    )


def test_disk_estimates_share_one_bitrate_model(tmp_path: Path) -> None:
    source = _source(tmp_path, duration=10.0, bitrate=8_000_000)

    assert budget.estimate_encoded_bytes(source) == 10_000_000
    assert budget.estimate_work_bytes(source) == 13_000_000


def test_disk_reservations_sum_and_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    first = budget.DiskReservation.acquire(tmp_path, "run-a", 60)
    try:
        with pytest.raises(budget.InsufficientDiskReservation) as exc_info:
            budget.DiskReservation.acquire(tmp_path, "run-b", 50)
        assert exc_info.value.already_reserved == 60
    finally:
        first.release()

    replacement = budget.DiskReservation.acquire(tmp_path, "run-b", 50)
    replacement.release()


def test_concurrent_disk_admission_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    start = threading.Barrier(2)
    acquired: list[budget.DiskReservation] = []
    rejected: list[budget.InsufficientDiskReservation] = []

    def attempt(run_id: str) -> None:
        start.wait(timeout=2)
        try:
            acquired.append(budget.DiskReservation.acquire(tmp_path, run_id, 60))
        except budget.InsufficientDiskReservation as exc:
            rejected.append(exc)

    threads = [threading.Thread(target=attempt, args=(f"run-{idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    try:
        assert all(not thread.is_alive() for thread in threads)
        assert len(acquired) == 1
        assert len(rejected) == 1
    finally:
        for reservation in acquired:
            reservation.release()


def test_dead_local_disk_reservation_is_reclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    stale = budget.DiskReservation.acquire(tmp_path, "dead", 60)
    stale.lock_path.write_text(json.dumps({
        "schema_version": budget.DISK_RESERVATION_SCHEMA,
        "run_id": "dead",
        "pid": 2_000_000_001,
        "hostname": socket.gethostname(),
        "filesystem_id": stale.filesystem_id,
        "reserved_bytes": 60,
    }), encoding="utf-8")

    replacement = budget.DiskReservation.acquire(tmp_path, "replacement", 50)
    assert not stale._released
    replacement.release()


def test_foreign_disk_reservation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    foreign = budget.DiskReservation.acquire(tmp_path, "remote", 60)
    foreign.lock_path.write_text(json.dumps({
        "schema_version": budget.DISK_RESERVATION_SCHEMA,
        "run_id": "remote",
        "pid": 2_000_000_001,
        "hostname": "another-host",
        "filesystem_id": foreign.filesystem_id,
        "reserved_bytes": 60,
    }), encoding="utf-8")

    with pytest.raises(budget.InsufficientDiskReservation):
        budget.DiskReservation.acquire(tmp_path, "local", 50)

    assert foreign.lock_path.exists()


def test_malformed_disk_reservation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    malformed = budget.DiskReservation.acquire(tmp_path, "bad", 10)
    malformed.lock_path.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(budget.DiskReservationError, match="malformed"):
        budget.DiskReservation.acquire(tmp_path, "next", 10)

    assert malformed.lock_path.exists()


def test_disk_record_fsync_failure_removes_partial_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    warm = budget.DiskReservation.acquire(tmp_path, "warm", 1)
    reservations_dir = warm.lock_path.parent
    warm.release()
    real_fsync = os.fsync
    calls = 0

    def fail_record_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected disk record fsync failure")
        real_fsync(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fsync", fail_record_fsync)
        with pytest.raises(budget.DiskReservationError, match="could not persist"):
            budget.DiskReservation.acquire(tmp_path, "failed", 10)

    assert list(reservations_dir.glob("reservation-*.lock")) == []
    replacement = budget.DiskReservation.acquire(tmp_path, "replacement", 10)
    replacement.release()


def test_disk_record_name_collision_never_deletes_existing_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_free(monkeypatch, 100)
    warm = budget.DiskReservation.acquire(tmp_path, "warm", 1)
    reservations_dir = warm.lock_path.parent
    filesystem_id = warm.filesystem_id
    warm.release()
    collision = reservations_dir / f"reservation-{os.getpid()}-fixed.lock"
    collision_payload = {
        "schema_version": budget.DISK_RESERVATION_SCHEMA,
        "run_id": "existing-owner",
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "filesystem_id": filesystem_id,
        "reserved_bytes": 1,
    }
    collision.write_text(json.dumps(collision_payload), encoding="utf-8")
    monkeypatch.setattr(budget.secrets, "token_hex", lambda _size: "fixed")

    with pytest.raises(budget.DiskReservationError, match="could not persist"):
        budget.DiskReservation.acquire(tmp_path, "new-owner", 10)

    assert json.loads(collision.read_text(encoding="utf-8")) == collision_payload
