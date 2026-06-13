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


_MAX_HOST_LEN = 64


def _safe_host_name(raw: str) -> str:
    """Sanitise a hostname against shared-FS path traversal.

    5.2 (v0.5.5): the queue concatenates ``self.host`` into in_progress/
    and failed/ paths and into the ``<host>.alive`` filename. POSIX
    hostnames per RFC 952/1123 must not contain ``/`` or ``..``, but
    ``socket.gethostname()`` returns whatever the kernel reports and a
    deliberately-set hostname (or a user-supplied ``host=`` kwarg)
    could escape the queue layout. Trim length, neutralise separators,
    fall back to ``"unknown"`` on empty input.
    """
    cleaned = raw.replace("/", "_").replace("\\", "_").replace("..", "__")
    cleaned = cleaned.strip(" .").replace("\x00", "_")
    return cleaned[:_MAX_HOST_LEN] or "unknown"


class FileQueue:
    """Producer/consumer file queue backed by a shared filesystem."""

    def __init__(self, root: Path, *, host: str | None = None) -> None:
        self.layout = queue_layout(root)
        self.host = _safe_host_name(host or socket.gethostname())
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

        A7 (v0.5.5): symlinks in ``pending/`` are rejected after rename.
        On a multi-tenant shared FS an adversarial process could drop a
        symlink in ``pending/`` pointing to ``/etc/shadow`` or any
        readable file outside the queue root. ``os.rename`` moves the
        symlink itself (not the target), but downstream ``ffprobe -i
        <leased>`` follows it and the contents reach the worker's log
        files. We delete the symlink and continue to the next candidate.
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
            if dest.is_symlink():
                # Hostile or accidental symlink. Drop it, do NOT return
                # it to pending (an attacker could re-place it). Log via
                # the side-channel ``.rejected_symlinks`` marker so an
                # operator can audit.
                try:
                    dest.unlink()
                except OSError:
                    pass
                marker = self.layout.in_progress / ".rejected_symlinks.log"
                try:
                    with marker.open("a", encoding="utf-8") as fh:
                        fh.write(
                            f"{time.time():.0f} {self.host} {candidate.name}\n"
                        )
                except OSError:
                    pass
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

        A8 (v0.5.5): narrow the reaper race window. Previously the alive
        mtime was checked once at top-of-loop; between that check and
        the per-file ``os.rename`` the original host could resume,
        touch its heartbeat, and start a fresh lease — only to have its
        input file moved away mid-operation. We re-check the heartbeat
        AFTER snapshotting the candidate list and bail out if the host
        is now liveness-positive.

        Note we deliberately do NOT add a per-file mtime grace window:
        ``os.rename`` preserves a file's content mtime, so a freshly-
        leased input would still appear "old" if it was an old archive
        clip — file mtime is a poor proxy for lease liveness.
        """
        now = time.time()
        count = 0
        for host_dir in sorted(self.layout.in_progress.iterdir()):
            if not host_dir.is_dir():
                continue
            alive = self.layout.in_progress / f"{host_dir.name}.alive"
            alive_existed = alive.exists()
            if alive_existed:
                if now - alive.stat().st_mtime <= stale_sec:
                    continue
            else:
                # No heartbeat anchor — worker crashed before/at startup.
                # Use the oldest file's mtime in the host_dir as a
                # conservative liveness proxy, and only reclaim if even
                # that is older than stale_sec. Empty dirs are skipped.
                try:
                    mtimes = [f.stat().st_mtime for f in host_dir.iterdir()]
                except OSError:
                    continue
                if not mtimes or now - min(mtimes) <= stale_sec:
                    continue
            candidates = list(host_dir.iterdir())
            # A8 re-check: if the host touched .alive between the
            # top-of-loop check and now, bail out — they are recovering
            # and we should not move their files.
            if alive_existed:
                try:
                    refreshed = alive.stat().st_mtime
                except FileNotFoundError:
                    refreshed = 0.0
                if time.time() - refreshed <= stale_sec:
                    continue
            for f in candidates:
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
