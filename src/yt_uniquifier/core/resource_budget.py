"""Filesystem-coordinated encoder and temporary-disk resource budgets."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from yt_uniquifier.core.models import SourceMeta
from yt_uniquifier.core.output_reservation import (
    RunAdmission,
    RunAdmissionError,
    RunAdmissionFull,
    _is_dead_local_owner,
    _read_owner,
    _reclaim_dead_local_lock,
    _release_exact_owner,
)
from yt_uniquifier.core.utils.file_ops import retry_sharing_lock

DEFAULT_RESOURCE_LOCK_DIR = (
    Path.home() / ".cache" / "yt_uniquifier" / "resource-admission"
)
DISK_RESERVATION_SCHEMA = 1
WORK_BYTES_OVERHEAD_FACTOR = 1.3
DISK_SAFETY_FACTOR = 1.1
DEFAULT_TARGET_BITRATE_BPS = 8_000_000
DEFAULT_AUDIO_BITRATE_BPS = 256_000


class DiskReservationError(RuntimeError):
    """Disk reservation storage could not be inspected or updated safely."""


class InsufficientDiskReservation(DiskReservationError):
    """Unreserved free bytes are below the requested safety budget."""

    def __init__(self, required: int, free: int, already_reserved: int) -> None:
        self.required = required
        self.free = free
        self.already_reserved = already_reserved
        available = max(0, free - already_reserved)
        super().__init__(
            f"required {required} bytes but only {available} unreserved bytes remain"
        )


def resource_lock_root() -> Path:
    """Return the common local registry, honoring the deployment override."""
    override = os.environ.get("YT_UNIQ_RESOURCE_LOCK_DIR")
    return Path(override).expanduser() if override else DEFAULT_RESOURCE_LOCK_DIR


def resource_pool_dir(key: str) -> Path:
    """Map an arbitrary resource identity to a readable, filesystem-safe path."""
    slug = "".join(char if char.isalnum() else "-" for char in key).strip("-")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return resource_lock_root() / f"{slug[:32] or 'resource'}-{digest}"


def estimate_audio_bytes(source: SourceMeta) -> int:
    """Estimate every audio stream at no less than the delivery bitrate."""
    def delivery_bitrate(stream_channels: int) -> int:
        if stream_channels <= 1:
            return 128_000
        if stream_channels == 2:
            return 384_000
        if stream_channels <= 6:
            return 512_000
        return stream_channels * 128_000

    bitrate = sum(
        max(
            stream.bit_rate or DEFAULT_AUDIO_BITRATE_BPS,
            delivery_bitrate(stream.channels),
        )
        for stream in source.audio
    )
    return int(max(0.0, source.duration_sec) * (bitrate / 8.0))


def estimate_encoded_bytes(source: SourceMeta) -> int:
    """Estimate final video plus audio bytes from the best known bitrates."""
    # A highly compressed source can expand substantially when decoded,
    # transformed and encoded at CRF/CQ.  Do not let a small source bitrate
    # defeat the conservative pre-first-segment reservation; measured segment
    # rates refine this estimate as soon as durable output exists.
    source_bitrate = (
        source.video[0].bit_rate if source.video and source.video[0].bit_rate else 0
    )
    bitrate = max(DEFAULT_TARGET_BITRATE_BPS, source_bitrate)
    video_bytes = int(max(0.0, source.duration_sec) * (bitrate / 8.0))
    return video_bytes + estimate_audio_bytes(source)


def estimate_work_bytes(source: SourceMeta) -> int:
    """Estimate segment/audio workspace bytes before the final concat."""
    return int(estimate_encoded_bytes(source) * WORK_BYTES_OVERHEAD_FACTOR)


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise DiskReservationError(
                f"could not resolve an existing filesystem ancestor for {path}"
            )
        current = parent
    return current


def _filesystem_identity(path: Path) -> tuple[str, Path]:
    probe = _existing_ancestor(path)
    try:
        device = probe.stat().st_dev
    except OSError as exc:
        raise DiskReservationError(
            f"could not identify filesystem for {probe}: {exc}"
        ) from exc
    return f"device-{device}", probe


def _acquire_mutex(
    pool_dir: Path,
    owner_id: str,
    cancelled: Callable[[], bool] | None,
) -> RunAdmission:
    mutex_pool = pool_dir / "mutex"
    while True:
        if cancelled is not None and cancelled():
            raise DiskReservationError("cancelled while waiting for disk registry")
        try:
            return RunAdmission.acquire(mutex_pool, owner_id, 1)
        except RunAdmissionFull:
            time.sleep(0.1)
        except RunAdmissionError as exc:
            raise DiskReservationError(
                f"disk reservation mutex is unavailable: {exc}"
            ) from exc


@dataclass
class DiskReservation:
    """One variable-size reservation against a filesystem's current free bytes."""

    lock_path: Path
    run_id: str
    pid: int
    hostname: str
    reserved_bytes: int
    filesystem_id: str
    probe_path: Path
    _released: bool = False
    _update_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )

    @classmethod
    def acquire(
        cls,
        target: Path,
        run_id: str,
        required_bytes: int,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> DiskReservation:
        if not run_id:
            raise ValueError("run_id must not be empty")
        if required_bytes < 0:
            raise ValueError("required_bytes must be >= 0")
        filesystem_id, probe = _filesystem_identity(target)
        pool_dir = resource_pool_dir(f"disk:{filesystem_id}")
        try:
            pool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise DiskReservationError(
                f"could not create disk reservation pool: {exc}"
            ) from exc
        if pool_dir.is_symlink() or not pool_dir.is_dir():
            raise DiskReservationError("disk reservation pool is not a directory")

        pid = os.getpid()
        hostname = socket.gethostname()
        mutex_owner = f"disk-mutex-{pid}-{secrets.token_hex(8)}"
        mutex = _acquire_mutex(pool_dir, mutex_owner, cancelled)
        try:
            reservations_dir = pool_dir / "reservations"
            try:
                reservations_dir.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise DiskReservationError(
                    f"could not create disk reservation storage: {exc}"
                ) from exc
            if reservations_dir.is_symlink() or not reservations_dir.is_dir():
                raise DiskReservationError(
                    "disk reservation storage is not a directory"
                )
            active_bytes = cls._active_reserved_bytes(
                reservations_dir, filesystem_id, hostname,
            )
            try:
                free = shutil.disk_usage(probe).free
            except OSError as exc:
                raise DiskReservationError(
                    f"could not query free disk bytes for {probe}: {exc}"
                ) from exc
            if max(0, free - active_bytes) < required_bytes:
                raise InsufficientDiskReservation(
                    required_bytes, free, active_bytes,
                )

            lock_path = reservations_dir / (
                f"reservation-{pid}-{secrets.token_hex(12)}.lock"
            )
            payload = {
                "schema_version": DISK_RESERVATION_SCHEMA,
                "run_id": run_id,
                "pid": pid,
                "hostname": hostname,
                "filesystem_id": filesystem_id,
                "reserved_bytes": required_bytes,
                "acquired_at": time.time(),
            }
            created = False
            try:
                fd = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                created = True
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException as exc:
                if created:
                    with contextlib.suppress(OSError):
                        lock_path.unlink(missing_ok=True)
                if isinstance(exc, Exception):
                    raise DiskReservationError(
                        f"could not persist disk reservation: {exc}"
                    ) from exc
                raise
            return cls(
                lock_path=lock_path,
                run_id=run_id,
                pid=pid,
                hostname=hostname,
                reserved_bytes=required_bytes,
                filesystem_id=filesystem_id,
                probe_path=probe,
            )
        finally:
            mutex.release()

    @staticmethod
    def _active_reserved_bytes(
        reservations_dir: Path,
        filesystem_id: str,
        hostname: str,
    ) -> int:
        total = 0
        for lock_path in reservations_dir.glob("reservation-*.lock"):
            try:
                record_stat = retry_sharing_lock(lock_path.lstat)
            except FileNotFoundError:
                # Owners release outside this scan's mutex. Disappearance is
                # valid completion, not a malformed/non-regular record.
                continue
            if not stat.S_ISREG(record_stat.st_mode):
                raise DiskReservationError(
                    f"disk reservation record is not a regular file: {lock_path}"
                )
            owner = _read_owner(lock_path)
            if owner is None:
                continue
            if _is_dead_local_owner(owner, hostname):
                reclaim_path = lock_path.with_name(f".{lock_path.name}.reclaim")
                if _reclaim_dead_local_lock(
                    lock_path, reclaim_path, hostname,
                ):
                    continue
            try:
                schema = int(owner["schema_version"])
                owner_filesystem = str(owner["filesystem_id"])
                reserved = int(owner["reserved_bytes"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DiskReservationError(
                    f"disk reservation record is malformed: {lock_path}"
                ) from exc
            if (
                schema != DISK_RESERVATION_SCHEMA
                or owner_filesystem != filesystem_id
                or reserved < 0
            ):
                raise DiskReservationError(
                    f"disk reservation record has incompatible data: {lock_path}"
                )
            total += reserved
        return total

    def resize(
        self,
        required_bytes: int,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        """Atomically replace this reservation with a new future-byte budget.

        Files already written by the run are reflected in ``disk_usage().free``;
        callers therefore pass only the estimated bytes still to be written.
        Growth is admitted against every *other* active reservation while shrink
        updates make capacity available to concurrent runs immediately.
        """
        if required_bytes < 0:
            raise ValueError("required_bytes must be >= 0")
        with self._update_lock:
            if self._released:
                raise DiskReservationError("cannot resize a released reservation")
            pool_dir = self.lock_path.parent.parent
            mutex_owner = f"disk-resize-{self.pid}-{secrets.token_hex(8)}"
            mutex = _acquire_mutex(pool_dir, mutex_owner, cancelled)
            try:
                owner = _read_owner(self.lock_path)
                try:
                    exact_owner = (
                        owner is not None
                        and str(owner["run_id"]) == self.run_id
                        and int(owner["pid"]) == self.pid
                        and str(owner["hostname"]) == self.hostname
                        and str(owner["filesystem_id"]) == self.filesystem_id
                        and int(owner["schema_version"]) == DISK_RESERVATION_SCHEMA
                        and int(owner["reserved_bytes"]) == self.reserved_bytes
                    )
                except (KeyError, TypeError, ValueError):
                    exact_owner = False
                if not exact_owner:
                    raise DiskReservationError(
                        "disk reservation ownership changed during resize"
                    )
                assert owner is not None

                active_bytes = self._active_reserved_bytes(
                    self.lock_path.parent,
                    self.filesystem_id,
                    self.hostname,
                )
                other_reserved = max(0, active_bytes - self.reserved_bytes)
                try:
                    free = shutil.disk_usage(self.probe_path).free
                except OSError as exc:
                    raise DiskReservationError(
                        f"could not query free disk bytes for {self.probe_path}: {exc}"
                    ) from exc
                if max(0, free - other_reserved) < required_bytes:
                    raise InsufficientDiskReservation(
                        required_bytes,
                        free,
                        other_reserved,
                    )

                payload = dict(owner)
                payload["reserved_bytes"] = required_bytes
                payload["updated_at"] = time.time()
                replacement = self.lock_path.with_name(
                    f".{self.lock_path.name}.{secrets.token_hex(8)}.tmp"
                )
                created = False
                try:
                    fd = os.open(
                        replacement,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    created = True
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle, sort_keys=True)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    retry_sharing_lock(lambda: os.replace(replacement, self.lock_path))
                    created = False
                except BaseException as exc:
                    if created:
                        with contextlib.suppress(OSError):
                            replacement.unlink(missing_ok=True)
                    if isinstance(exc, Exception):
                        raise DiskReservationError(
                            f"could not update disk reservation: {exc}"
                        ) from exc
                    raise
                self.reserved_bytes = required_bytes
            finally:
                mutex.release()

    def release(self) -> None:
        with self._update_lock:
            if self._released:
                return
            if _release_exact_owner(
                self.lock_path,
                run_id=self.run_id,
                pid=self.pid,
                hostname=self.hostname,
            ):
                self._released = True

    def __enter__(self) -> DiskReservation:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
