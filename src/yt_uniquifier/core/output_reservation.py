"""Cross-process reservation for one final output path.

The web API can run in multiple processes that do not share an in-memory job
registry. A small owner-only lock file beside the output provides the same atomic
``O_EXCL`` boundary to every process using that filesystem.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_uniquifier.core.checkpoint import _pid_alive
from yt_uniquifier.core.errors import YtUniquifierError

_RESERVATION_DIR = ".yt_uniquifier-reservations"
_READ_RETRIES = 3


class OutputReservationError(YtUniquifierError):
    """Output reservation could not be acquired or persisted."""


class OutputReservationConflict(OutputReservationError):
    """Another live process already owns this output path."""


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
                ) and cls._reclaim_dead_local_lock(
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

    @staticmethod
    def _reclaim_dead_local_lock(
        lock_path: Path,
        reclaim_path: Path,
        hostname: str,
    ) -> bool:
        """Serialise stale-owner removal so two reclaimers cannot steal a new lock."""
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

    def release(self) -> None:
        """Release the reservation if the lock file still names this owner."""
        if self._released:
            return
        owner = _read_owner(self.lock_path)
        if owner is None:
            self._released = True
            return
        try:
            matches = (
                str(owner["run_id"]) == self.run_id
                and int(owner["pid"]) == self.pid
                and str(owner["hostname"]) == self.hostname
            )
        except (KeyError, TypeError, ValueError):
            return
        if matches:
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                return
        self._released = True

    def __enter__(self) -> OutputReservation:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
