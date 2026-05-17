"""Shared-FS work queue with atomic-rename leasing.

Designed for two or more machines that mount a common filesystem (NFSv4
with `noac`, ZFS, ext4 on shared block storage). No redis, no sqlite, no
external coordinator. The atomicity contract is POSIX `rename(2)` across
two directories on the same FS.

Layout:

    <root>/
    ├── pending/                    files waiting for a worker
    ├── in_progress/
    │   ├── <host>/                 files claimed by this host
    │   └── <host>.alive            mtime-as-heartbeat
    ├── done/                       completed (kept as a marker)
    └── failed/
        └── <host>/
            ├── <input>
            └── <input>.err.txt
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.errors import YtUniquifierError


class QueueError(YtUniquifierError):
    """Queue layout / atomicity / leasing failure."""


@dataclass(frozen=True)
class QueueLayout:
    root: Path
    pending: Path
    in_progress: Path
    done: Path
    failed: Path


def queue_layout(root: Path) -> QueueLayout:
    return QueueLayout(
        root=root,
        pending=root / "pending",
        in_progress=root / "in_progress",
        done=root / "done",
        failed=root / "failed",
    )


def init_queue(root: Path) -> QueueLayout:
    """Create the layout under root and verify atomic rename works."""
    layout = queue_layout(root)
    for d in (layout.pending, layout.in_progress, layout.done, layout.failed):
        d.mkdir(parents=True, exist_ok=True)
    _verify_atomic_rename(root)
    return layout


def _verify_atomic_rename(root: Path) -> None:
    """Fail fast if the FS doesn't support cross-directory atomic rename.

    Older NFS clients with attribute caching, S3 fuse mounts, and some
    SMB shares silently fall back to copy+delete, which breaks the lease
    invariant. We try a real `os.rename` between two subdirectories and
    raise if anything looks wrong.
    """
    src = root / ".rename_probe_src"
    dst = root / "pending" / ".rename_probe_dst"
    src.write_text("probe", encoding="utf-8")
    try:
        os.rename(src, dst)
    except OSError as exc:
        src.unlink(missing_ok=True)
        raise QueueError(
            f"shared FS at {root} does not support atomic cross-dir rename: {exc}. "
            "Required for leasing — use NFSv4 with `noac`, ZFS, or local ext4."
        ) from exc
    if not dst.exists() or src.exists():
        dst.unlink(missing_ok=True)
        raise QueueError(
            f"rename test produced unexpected state on {root}: source not removed "
            "or destination missing. Filesystem is not POSIX-rename-atomic."
        )
    dst.unlink(missing_ok=True)


class FileQueue:
    """Producer/consumer file queue backed by a shared filesystem."""

    def __init__(self, root: Path, *, host: str | None = None) -> None:
        self.layout = queue_layout(root)
        self.host = host or socket.gethostname()
        self.host_dir = self.layout.in_progress / self.host
        self.host_dir.mkdir(parents=True, exist_ok=True)

    # ---- producer ---------------------------------------------------------

    def add(self, path: Path) -> Path:
        """Hard-link or copy `path` into pending/. Returns the queued file path.

        Raises FileExistsError if a file with the same name is already queued.
        """
        if not path.exists():
            raise FileNotFoundError(path)
        dest = self.layout.pending / path.name
        if dest.exists():
            raise FileExistsError(f"already queued: {dest}")
        try:
            os.link(path, dest)
        except OSError:
            import shutil
            shutil.copy2(path, dest)
        return dest

    # ---- consumer ---------------------------------------------------------

    def lease(self) -> Path | None:
        """Atomically claim one pending file into the host's in_progress dir.

        Returns the new path, or None if the queue is empty. POSIX `rename`
        is the synchronisation point: between concurrent workers, exactly
        one wins each candidate.
        """
        for candidate in sorted(self.layout.pending.iterdir()):
            if candidate.name.startswith("."):
                continue
            dest = self.host_dir / candidate.name
            try:
                os.rename(candidate, dest)
            except (OSError, FileNotFoundError):
                # Another worker beat us to this file.
                continue
            return dest
        return None

    def heartbeat(self) -> None:
        """Touch <host>.alive so the reaper knows we're still working."""
        alive = self.layout.in_progress / f"{self.host}.alive"
        alive.touch()

    def release_done(self, leased: Path) -> Path:
        """Move a leased file into done/."""
        dest = self.layout.done / leased.name
        os.rename(leased, dest)
        return dest

    def release_failed(self, leased: Path, error: str) -> Path:
        """Move a leased file into failed/<host>/ and write the error trace."""
        host_failed = self.layout.failed / self.host
        host_failed.mkdir(parents=True, exist_ok=True)
        dest = host_failed / leased.name
        os.rename(leased, dest)
        (host_failed / f"{leased.name}.err.txt").write_text(error, encoding="utf-8")
        return dest

    # ---- maintenance ------------------------------------------------------

    def reap_stale(self, *, stale_sec: int = 300) -> int:
        """Recover files from dead workers' in_progress/<host>/ back to pending.

        A host is considered dead if its <host>.alive mtime is older than
        stale_sec. Returns the count of files relocated. Safe to call from
        any worker — losing race conditions reduce to "the file was already
        relocated", which the next lease iteration handles.
        """
        now = time.time()
        count = 0
        for host_dir in sorted(self.layout.in_progress.iterdir()):
            if not host_dir.is_dir():
                continue
            alive = self.layout.in_progress / f"{host_dir.name}.alive"
            if not alive.exists():
                continue
            if now - alive.stat().st_mtime <= stale_sec:
                continue
            for f in list(host_dir.iterdir()):
                try:
                    os.rename(f, self.layout.pending / f.name)
                    count += 1
                except OSError:
                    continue
            alive.unlink(missing_ok=True)
        return count

    def stats(self) -> dict[str, int]:
        def _count_files(d: Path) -> int:
            return sum(1 for x in d.iterdir() if not x.name.startswith("."))

        in_progress = 0
        for sub in self.layout.in_progress.iterdir():
            if sub.is_dir():
                in_progress += _count_files(sub)

        failed = 0
        for sub in self.layout.failed.iterdir():
            if sub.is_dir():
                failed += sum(
                    1 for x in sub.iterdir() if not x.name.endswith(".err.txt")
                )

        return {
            "pending": _count_files(self.layout.pending),
            "in_progress": in_progress,
            "done": _count_files(self.layout.done),
            "failed": failed,
        }
