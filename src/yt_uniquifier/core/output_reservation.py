"""Cross-process reservations for web admission and final output paths.

The web API can run in multiple processes that do not share an in-memory job
registry. Owner-only lock files under the shared output directory provide the same
atomic ``O_EXCL`` boundary to every process using that filesystem.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_uniquifier.core.checkpoint import _pid_alive
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.utils.file_ops import retry_sharing_lock

_RESERVATION_DIR = ".yt_uniquifier-reservations"
_ADMISSION_DIR = ".yt_uniquifier-admission"
_ADMISSION_SCHEMA = 1
_READ_RETRIES = 3


class OutputReservationError(YtUniquifierError):
    """Output reservation could not be acquired or persisted."""


class OutputReservationConflict(OutputReservationError):
    """Another live process already owns this output path."""


class RunAdmissionError(YtUniquifierError):
    """The shared admission pool is unavailable or misconfigured."""


class RunAdmissionFull(RunAdmissionError):
    """Every configured cross-process run slot is occupied."""


def _read_owner(path: Path) -> dict[str, Any] | None:
    """Read a complete lock payload, tolerating the short O_EXCL write window."""
    for attempt in range(_READ_RETRIES):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            if attempt + 1 < _READ_RETRIES:
                time.sleep(0.01)
                continue
            return {}
        return raw if isinstance(raw, dict) else {}
    return {}


def _is_dead_local_owner(owner: dict[str, Any], hostname: str) -> bool:
    try:
        owner_host = str(owner["hostname"])
        owner_pid = int(owner["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    return owner_host == hostname and not _pid_alive(owner_pid)


def _reclaim_dead_local_lock(
    lock_path: Path,
    reclaim_path: Path,
    hostname: str,
) -> bool:
    """Serialise stale-owner removal so reclaimers cannot steal a new lock."""
    try:
        guard_fd = os.open(
            reclaim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except OSError:
        return False
    os.close(guard_fd)
    try:
        current = _read_owner(lock_path)
        if current is None:
            return True
        if not _is_dead_local_owner(current, hostname):
            return False
        lock_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            reclaim_path.unlink(missing_ok=True)


def _release_exact_owner(
    lock_path: Path,
    *,
    run_id: str,
    pid: int,
    hostname: str,
) -> bool:
    """Delete *lock_path* only when all owner fields still match."""
    try:
        return retry_sharing_lock(lambda: _release_exact_owner_once(
            lock_path, run_id=run_id, pid=pid, hostname=hostname,
        ))
    except OSError:
        return False


def _release_exact_owner_once(
    lock_path: Path, *, run_id: str, pid: int, hostname: str,
) -> bool:
    # Recheck identity on every retry, never delete a newly replaced owner.
    owner = _read_owner(lock_path)
    if owner is None:
        return True
    try:
        matches = (
            str(owner["run_id"]) == run_id
            and int(owner["pid"]) == pid
            and str(owner["hostname"]) == hostname
        )
    except (KeyError, TypeError, ValueError):
        return False
    if not matches:
        return False
    try:
        lock_path.unlink(missing_ok=True)
    except PermissionError:
        raise
    except OSError:
        return False
    return True


@dataclass
class OutputReservation:
    """An acquired reservation that only its exact owner can release."""

    lock_path: Path
    run_id: str
    pid: int
    hostname: str
    _released: bool = False

    @classmethod
    def acquire(cls, output: Path, run_id: str) -> OutputReservation:
        """Atomically reserve *output* or raise ``OutputReservationConflict``."""
        if not run_id:
            raise ValueError("run_id must not be empty")
        reservation_dir = output.parent / _RESERVATION_DIR
        try:
            reservation_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputReservationError(
                f"could not create output reservation storage: {exc}",
            ) from exc
        if reservation_dir.is_symlink() or not reservation_dir.is_dir():
            raise OutputReservationError("output reservation storage is not a directory")
        lock_path = reservation_dir / f"{output.name}.lock"
        reclaim_path = reservation_dir / f".{output.name}.reclaim"
        pid = os.getpid()
        hostname = socket.gethostname()
        payload = json.dumps({
            "schema_version": 1,
            "run_id": run_id,
            "pid": pid,
            "hostname": hostname,
            "acquired_at": time.time(),
        })

        while True:
            try:
                fd = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except (FileExistsError, PermissionError) as exc:
                owner = _read_owner(lock_path)
                if owner is None:
                    if isinstance(exc, PermissionError):
                        raise OutputReservationError(
                            f"could not access output reservation: {exc}",
                        ) from exc
                    continue
                if _is_dead_local_owner(
                    owner, hostname,
                ) and _reclaim_dead_local_lock(
                    lock_path, reclaim_path, hostname,
                ):
                    continue
                raise OutputReservationConflict(
                    f"output {output.name!r} is already reserved",
                ) from None
            except OSError as exc:
                raise OutputReservationError(
                    f"could not reserve output {output.name!r}: {exc}",
                ) from exc

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException as exc:
                with contextlib.suppress(OSError):
                    lock_path.unlink(missing_ok=True)
                if isinstance(exc, Exception):
                    raise OutputReservationError(
                        f"could not persist output reservation: {exc}",
                    ) from exc
                raise
            return cls(
                lock_path=lock_path,
                run_id=run_id,
                pid=pid,
                hostname=hostname,
            )

    def release(self) -> None:
        """Release the reservation if the lock file still names this owner."""
        if self._released:
            return
        if _release_exact_owner(
            self.lock_path,
            run_id=self.run_id,
            pid=self.pid,
            hostname=self.hostname,
        ):
            self._released = True

    def __enter__(self) -> OutputReservation:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


@dataclass
class RunAdmission:
    """One exact-owner slot in a filesystem-wide web run capacity pool."""

    lock_path: Path
    run_id: str
    pid: int
    hostname: str
    capacity: int
    _released: bool = False

    @classmethod
    def acquire(
        cls,
        output_dir: Path,
        run_id: str,
        capacity: int,
    ) -> RunAdmission:
        """Acquire one shared slot or raise ``RunAdmissionFull``."""
        if not run_id:
            raise ValueError("run_id must not be empty")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        admission_dir = output_dir / _ADMISSION_DIR
        try:
            admission_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise RunAdmissionError(
                f"could not create shared admission storage: {exc}",
            ) from exc
        if admission_dir.is_symlink() or not admission_dir.is_dir():
            raise RunAdmissionError("shared admission storage is not a directory")

        cls._ensure_capacity(admission_dir, capacity)
        pid = os.getpid()
        hostname = socket.gethostname()
        payload = json.dumps({
            "schema_version": _ADMISSION_SCHEMA,
            "run_id": run_id,
            "pid": pid,
            "hostname": hostname,
            "acquired_at": time.time(),
        })
        for slot in range(capacity):
            lock_path = admission_dir / f"slot-{slot:04d}.lock"
            reclaim_path = admission_dir / f".slot-{slot:04d}.reclaim"
            while True:
                try:
                    fd = os.open(
                        lock_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except (FileExistsError, PermissionError) as exc:
                    try:
                        lock_mode = lock_path.lstat().st_mode
                    except FileNotFoundError:
                        # Another owner can release the slot between our O_EXCL
                        # collision and the metadata check.  That is ordinary
                        # contention, so retry the atomic create instead of
                        # reporting a corrupt admission pool.
                        if isinstance(exc, PermissionError):
                            raise RunAdmissionError(
                                f"could not access shared admission slot: {exc}"
                            ) from exc
                        continue
                    except OSError as stat_exc:
                        raise RunAdmissionError(
                            f"could not inspect shared admission slot: {stat_exc}"
                        ) from stat_exc
                    if not stat.S_ISREG(lock_mode):
                        raise RunAdmissionError(
                            f"shared admission slot is not a regular file: {lock_path}"
                        ) from None
                    owner = _read_owner(lock_path)
                    if owner is None:
                        continue
                    if _is_dead_local_owner(
                        owner, hostname,
                    ) and _reclaim_dead_local_lock(
                        lock_path, reclaim_path, hostname,
                    ):
                        continue
                    break
                except OSError as exc:
                    raise RunAdmissionError(
                        f"could not acquire shared admission slot: {exc}",
                    ) from exc

                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                except BaseException as exc:
                    with contextlib.suppress(OSError):
                        lock_path.unlink(missing_ok=True)
                    if isinstance(exc, Exception):
                        raise RunAdmissionError(
                            f"could not persist shared admission slot: {exc}",
                        ) from exc
                    raise
                return cls(
                    lock_path=lock_path,
                    run_id=run_id,
                    pid=pid,
                    hostname=hostname,
                    capacity=capacity,
                )
        raise RunAdmissionFull(
            f"shared run capacity of {capacity} is fully occupied"
        )

    @staticmethod
    def _ensure_capacity(admission_dir: Path, capacity: int) -> None:
        """Create or validate the immutable capacity contract for this pool."""
        manifest = admission_dir / "capacity.json"
        payload = {
            "schema_version": _ADMISSION_SCHEMA,
            "capacity": capacity,
        }
        while True:
            try:
                fd = os.open(
                    manifest,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except (FileExistsError, PermissionError):
                if manifest.is_symlink() or not manifest.is_file():
                    raise RunAdmissionError(
                        "shared admission capacity record is not a regular file"
                    ) from None
                current = _read_owner(manifest)
                if current is None:
                    continue
                if (
                    current.get("schema_version") != _ADMISSION_SCHEMA
                    or current.get("capacity") != capacity
                ):
                    raise RunAdmissionError(
                        "shared admission capacity does not match this server; "
                        "stop all instances before changing max_concurrent_runs"
                    ) from None
                return
            except OSError as exc:
                raise RunAdmissionError(
                    f"could not persist shared admission capacity: {exc}",
                ) from exc

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException as exc:
                with contextlib.suppress(OSError):
                    manifest.unlink(missing_ok=True)
                if isinstance(exc, Exception):
                    raise RunAdmissionError(
                        f"could not persist shared admission capacity: {exc}",
                    ) from exc
                raise
            return

    def release(self) -> None:
        """Release the slot only while its complete owner identity matches."""
        if self._released:
            return
        if _release_exact_owner(
            self.lock_path,
            run_id=self.run_id,
            pid=self.pid,
            hostname=self.hostname,
        ):
            self._released = True

    def __enter__(self) -> RunAdmission:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
