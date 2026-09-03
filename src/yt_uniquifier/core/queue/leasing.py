"""Shared-FS work queue with atomic-rename leasing.

Designed for two or more machines that mount a common filesystem (NFSv4
with `noac`, ZFS, ext4 on shared block storage). No redis, no sqlite, no
external coordinator. The atomicity contract is POSIX `rename(2)` across
two directories on the same FS.

Layout:

    <root>/
    ├── pending/                    files waiting for a worker
    ├── in_progress/
    │   ├── <worker-id>/            files claimed by one worker process
    │   └── <worker-id>.alive       mtime-as-heartbeat
    ├── done/                       completed (kept as a marker)
    ├── .commits/                   durable output-publication journals
    └── failed/
        └── <worker-id>/
            ├── <input>
            └── <input>.err.txt
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.errors import YtUniquifierError

_log = logging.getLogger(__name__)
_COMMIT_JOURNAL_DIR = ".commits"
_COMMIT_JOURNAL_SCHEMA = 1


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
        explicit_host = host is not None
        self.host = _safe_host_name(host or socket.gethostname())
        if explicit_host:
            self.worker_id = self.host
        else:
            # Preserve the uniqueness suffix when a cloud runner reports a
            # hostname at (or beyond) the 64-character path-component policy.
            # Sanitising the combined string used to truncate PID+nonce away,
            # making every FileQueue instance on that host share one lease dir.
            suffix = f"-{os.getpid()}-{secrets.token_hex(4)}"
            host_prefix = self.host[:max(1, _MAX_HOST_LEN - len(suffix))]
            self.worker_id = f"{host_prefix}{suffix}"
        self.host_dir = self.layout.in_progress / self.worker_id
        self.host_dir.mkdir(parents=True, exist_ok=True)
        # B8 (v0.6.0): cached sorted candidate name list so lease() does
        # not re-list pending/ on every call. On NFSv4 noac a fresh
        # ``iterdir`` round-trips to the server (10-100 ms on a busy
        # queue); the cursor halves that traffic for typical drain runs.
        self._lease_cursor: list[str] = []

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

        B8 (v0.6.0): the candidate list is cached in ``_lease_cursor``
        and refreshed only when exhausted. A single drain run that
        pulls N files now needs ⌈N / batch⌉ ``iterdir`` calls instead
        of N. We re-list (not differential) so stale entries that lost
        races with other workers are dropped naturally; ``os.rename``
        on an already-leased file raises FileNotFoundError and we
        fall through to the next candidate.
        """
        if not self._lease_cursor:
            self._refresh_lease_cursor()
        while self._lease_cursor:
            name = self._lease_cursor.pop(0)
            # Dotfiles are filtered at refresh time, but keep a defensive
            # skip here in case a future code path injects names directly.
            if name.startswith("."):
                continue
            candidate = self.layout.pending / name
            dest = self.host_dir / name
            # v0.7 R9 — ``os.rename`` on POSIX is the canonical
            # synchronisation point (atomic via rename(2)). On Windows,
            # concurrent ``MoveFileEx`` calls against the same source
            # have been observed to *both* succeed under the GitHub
            # Actions CI runner — likely a metadata-cache race in the
            # underlying NTFS layer. We add a per-file ``.__lock__``
            # marker created with ``O_CREAT | O_EXCL`` (atomic on every
            # FS we ship to) BEFORE the rename. Whoever creates the
            # marker first owns the lease; everyone else falls through.
            # Leading dot so ``_refresh_lease_cursor``'s dotfile filter
            # naturally skips abandoned locks; otherwise a crashed
            # worker's leftover ``f0.mp4.__lock__`` would itself appear
            # as a candidate name on the next refresh.
            lock_path = candidate.with_name("." + candidate.name + ".__lock__")
            try:
                lock_fd = os.open(
                    str(lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except (FileExistsError, PermissionError):
                # Windows can return ``PermissionError`` instead of
                # ``FileExistsError`` if a peer's lock handle is still
                # being closed when we hit O_EXCL. Both mean "someone
                # else owns this candidate" — skip.
                continue
            os.close(lock_fd)
            try:
                os.rename(candidate, dest)
            except (OSError, FileNotFoundError):
                # Another worker beat us to this file or it was
                # removed externally. Cursor moves on; refresh happens
                # when the cursor exhausts.
                with contextlib.suppress(OSError):
                    os.unlink(lock_path)
                continue
            with contextlib.suppress(OSError):
                os.unlink(lock_path)
            if dest.is_symlink():
                # Hostile or accidental symlink. Drop it, do NOT return
                # it to pending (an attacker could re-place it). Log via
                # the side-channel ``.rejected_symlinks`` marker so an
                # operator can audit.
                with contextlib.suppress(OSError):
                    dest.unlink()
                marker = self.layout.in_progress / ".rejected_symlinks.log"
                with contextlib.suppress(OSError), marker.open(
                    "a", encoding="utf-8",
                ) as fh:
                    fh.write(
                        f"{time.time():.0f} {self.worker_id} {candidate.name}\n"
                    )
                continue
            return dest
        # Cursor exhausted without a successful lease — try one more
        # refresh in case workers added new files concurrently. If still
        # empty, the queue is genuinely drained.
        self._refresh_lease_cursor()
        if not self._lease_cursor:
            return None
        # Recursive single-step retry; bounded depth because the second
        # call sees the refreshed cursor and either leases or returns
        # None at the empty-after-refresh check above.
        return self.lease()

    def _refresh_lease_cursor(self) -> None:
        """B8 (v0.6.0): re-list pending/ into the cursor.

        Called when the cursor exhausts. Centralising the listing here
        makes the polling cadence on shared FS explicit and easy to
        change in one place (e.g. adding a TTL on long-running drains).
        """
        try:
            entries = list(self.layout.pending.iterdir())
        except OSError:
            self._lease_cursor = []
            return
        # Filter dotfiles at refresh time, not in the lease loop —
        # otherwise a queue containing only dotfiles would refresh
        # forever (lease pops dot, no rename, falls through, refreshes,
        # gets the same dots back).
        self._lease_cursor = sorted(
            p.name for p in entries if not p.name.startswith(".")
        )

    def heartbeat(self) -> None:
        """Touch <worker-id>.alive so the reaper tracks this process."""
        alive = self.layout.in_progress / f"{self.worker_id}.alive"
        alive.touch()

    def release_done(self, leased: Path) -> Path:
        """Move a leased file into done/."""
        dest = self.layout.done / leased.name
        os.rename(leased, dest)
        return dest

    def staged_output_path(self, output: Path) -> Path:
        """Return a worker-unique hidden output path beside the final file."""
        worker_key = hashlib.sha256(self.worker_id.encode("utf-8")).hexdigest()[:12]
        return output.with_name(
            f".{output.stem}.{worker_key}.part{output.suffix}"
        )

    def _commit_journal_dir(self) -> Path:
        journal_dir = self.layout.root / _COMMIT_JOURNAL_DIR
        try:
            journal_dir.mkdir(mode=0o770, parents=True, exist_ok=True)
        except OSError as exc:
            raise QueueError(f"could not create commit journal storage: {exc}") from exc
        if journal_dir.is_symlink() or not journal_dir.is_dir():
            raise QueueError("commit journal storage is not a directory")
        return journal_dir

    def _write_commit_journal(
        self,
        leased: Path,
        staged: Path,
        output: Path,
    ) -> tuple[Path, Path]:
        """Persist commit intent before the lease-to-done fencing rename."""
        journal_dir = self._commit_journal_dir()
        token = secrets.token_hex(8)
        journal = journal_dir / f"commit-{token}.json"
        fence = self.layout.done / f".commit-{token}.fence"
        temp = journal_dir / f".{journal.name}.{os.getpid()}.tmp"
        payload = {
            "schema_version": _COMMIT_JOURNAL_SCHEMA,
            "worker_id": self.worker_id,
            "input_name": leased.name,
            "staged_name": staged.name,
            "output_name": output.name,
            "fence_name": fence.name,
            "created_at": time.time(),
        }
        try:
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o640,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, journal)
        except BaseException as exc:
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                journal.unlink(missing_ok=True)
            if isinstance(exc, Exception):
                raise QueueError(f"could not persist output commit journal: {exc}") from exc
            raise
        return journal, fence

    @staticmethod
    def _remove_commit_journal(journal: Path) -> None:
        with contextlib.suppress(OSError):
            journal.unlink(missing_ok=True)

    @staticmethod
    def _journal_basename(payload: dict[str, object], field: str) -> str:
        value = payload.get(field)
        if (
            not isinstance(value, str)
            or value in {"", ".", ".."}
            or "/" in value
            or "\\" in value
            or Path(value).name != value
        ):
            raise QueueError(f"commit journal has invalid {field}")
        return value

    def _journal_owner_is_live(self, worker_id: str, stale_sec: int) -> bool:
        alive = self.layout.in_progress / f"{worker_id}.alive"
        try:
            age = time.time() - alive.stat().st_mtime
        except OSError:
            return False
        return age <= stale_sec

    def recover_commits(self, output_dir: Path, *, stale_sec: int = 300) -> int:
        """Reconcile durable output commits left by interrupted workers.

        A journal-specific hidden fence in ``done/`` proves that its staged
        output may be published. A same-named marker from an older run cannot
        authorize publication. Returns the number of journals resolved or
        safely discarded.
        """
        if stale_sec < 0:
            raise ValueError("stale_sec must be >= 0")
        journal_dir = self.layout.root / _COMMIT_JOURNAL_DIR
        if not journal_dir.exists():
            return 0
        if journal_dir.is_symlink() or not journal_dir.is_dir():
            raise QueueError("commit journal storage is not a directory")
        output_root = output_dir.resolve(strict=False)
        resolved = 0
        for journal in sorted(journal_dir.glob("commit-*.json")):
            if journal.is_symlink() or not journal.is_file():
                raise QueueError(f"commit journal entry is not a regular file: {journal}")
            try:
                payload = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QueueError(f"commit journal is unreadable: {journal}") from exc
            if not isinstance(payload, dict):
                raise QueueError(f"commit journal has invalid payload: {journal}")
            if payload.get("schema_version") != _COMMIT_JOURNAL_SCHEMA:
                raise QueueError(f"unsupported commit journal schema: {journal}")

            worker_id = self._journal_basename(payload, "worker_id")
            input_name = self._journal_basename(payload, "input_name")
            staged_name = self._journal_basename(payload, "staged_name")
            output_name = self._journal_basename(payload, "output_name")
            fence_name = self._journal_basename(payload, "fence_name")
            if not fence_name.startswith(".commit-") or not fence_name.endswith(".fence"):
                raise QueueError("commit journal has invalid fence_name")
            leased = self.layout.in_progress / worker_id / input_name
            pending = self.layout.pending / input_name
            done = self.layout.done / input_name
            fence = self.layout.done / fence_name
            staged = output_root / staged_name
            output = output_root / output_name

            if self._journal_owner_is_live(worker_id, stale_sec):
                continue

            if fence.exists():
                if fence.is_symlink() or not fence.is_file():
                    raise QueueError(f"commit fence is not a regular file: {fence}")
                if staged.exists():
                    if staged.is_symlink() or not staged.is_file():
                        raise QueueError(f"staged output is not a regular file: {staged}")
                    try:
                        os.replace(staged, output)
                    except FileNotFoundError:
                        # A concurrent recovery may have completed the same journal.
                        if not output.is_file() or output.is_symlink():
                            raise QueueError(
                                f"staged output disappeared during recovery: {staged}"
                            ) from None
                elif not output.is_file() or output.is_symlink():
                    raise QueueError(
                        f"fenced commit has neither staged nor final output: {journal}"
                    )
                try:
                    os.replace(fence, done)
                except FileNotFoundError:
                    # Another recovery may already have canonicalized the fence.
                    if not done.is_file() or done.is_symlink():
                        raise QueueError(
                            f"commit fence disappeared during recovery: {fence}"
                        ) from None
                self._remove_commit_journal(journal)
                resolved += 1
                continue

            if leased.exists():
                # The original owner is still processing or awaiting stale reaping.
                continue

            another_lease = any(
                candidate.is_dir() and (candidate / input_name).exists()
                for candidate in self.layout.in_progress.iterdir()
            )
            failed = any(
                candidate.is_dir() and (candidate / input_name).exists()
                for candidate in self.layout.failed.iterdir()
            )
            if pending.exists() or another_lease or failed:
                # No done fence: the old staged bytes must never be published.
                if staged.exists() and (staged.is_symlink() or staged.is_file()):
                    with contextlib.suppress(OSError):
                        staged.unlink()
                self._remove_commit_journal(journal)
                resolved += 1
                continue

            if (
                done.is_file()
                and not done.is_symlink()
                and output.is_file()
                and not output.is_symlink()
                and not staged.exists()
            ):
                # Publication and fence canonicalization completed before a crash.
                self._remove_commit_journal(journal)
                resolved += 1
                continue
            raise QueueError(f"commit journal cannot be reconciled safely: {journal}")
        return resolved

    def commit_output(self, leased: Path, staged: Path, output: Path) -> Path:
        """Fence a completed lease, then atomically publish its staged output.

        Moving ``leased`` to a journal-specific hidden fence is the
        synchronisation point. If a stale lease was already reaped, this worker
        cannot publish an obsolete result. The encoded file stays hidden until
        that ownership transition succeeds.
        """
        if leased.parent != self.host_dir or leased.is_symlink() or not leased.is_file():
            raise QueueError(f"lease ownership lost before output commit: {leased}")
        if staged.parent != output.parent or not staged.is_file():
            raise QueueError(f"staged output is missing or on a different directory: {staged}")
        output.parent.mkdir(parents=True, exist_ok=True)
        # Close the startup/recovery race: a peer must observe this commit owner
        # as live before the journal and done fence can become visible.
        self.heartbeat()
        journal, fence = self._write_commit_journal(leased, staged, output)

        try:
            os.rename(leased, fence)
        except OSError as exc:
            self._remove_commit_journal(journal)
            raise QueueError(f"lease ownership lost before output commit: {leased}") from exc
        try:
            os.replace(staged, output)
        except OSError:
            # Restore the lease so the caller can put it in failed/. If rollback
            # also fails, retain the journal and staged bytes for another worker.
            restored = False
            try:
                os.rename(fence, leased)
                restored = True
            except OSError as rollback_exc:
                _log.error(
                    "output publication and lease rollback both failed; "
                    "commit journal retained at %s: %s",
                    journal,
                    rollback_exc,
                )
            if restored:
                self._remove_commit_journal(journal)
            raise
        done = self.layout.done / leased.name
        try:
            os.replace(fence, done)
        except OSError as exc:
            _log.error(
                "output published but commit fence finalization failed; "
                "journal retained at %s: %s",
                journal,
                exc,
            )
            raise QueueError(f"could not finalize output commit: {done}") from exc
        self._remove_commit_journal(journal)
        return done

    def release_failed(self, leased: Path, error: str) -> Path:
        """Move a leased file into failed/<host>/ and write the error trace."""
        host_failed = self.layout.failed / self.worker_id
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
