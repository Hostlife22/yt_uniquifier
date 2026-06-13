"""A7 + A8 + 5.2 (v0.5.5) regression tests for queue/leasing.py.

A7: lease() must reject symlinks that arrive in pending/ (multi-tenant
shared FS: an adversarial process could drop a symlink to /etc/shadow
or any readable file outside the queue; ffprobe follows symlinks and
the contents would leak into worker logs).

A8: reap_stale() must narrow the window between deciding a host is dead
and renaming its in_progress files back to pending. We re-check the
heartbeat after listing files and grace-window any file with a fresh
mtime so a recovering worker doesn't have its in-flight input ripped
out.

5.2: hostnames that contain path separators (``/``, ``\\``, ``..``)
must be sanitised before being concatenated into queue paths and the
``<host>.alive`` filename.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from yt_uniquifier.core.queue.leasing import (
    FileQueue,
    _safe_host_name,
    init_queue,
)

# os.symlink on Windows requires admin OR Developer Mode privilege; GitHub
# Actions windows-latest runners run as a standard user without either.
# Skip the symlink-creating tests there. The production guard
# (FileQueue.lease rejecting symlinks) is exercised at the code level
# regardless — the runtime check still fires; we just can't fabricate
# the adversarial input on Windows CI.
needs_symlink = pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.symlink requires admin/Developer Mode on Windows",
)


@pytest.fixture
def queue_root(tmp_path: Path) -> Path:
    root = tmp_path / "q"
    init_queue(root)
    return root


# -------------------------------------------------------------------- A7

@needs_symlink
def test_lease_rejects_symlink_in_pending(
    queue_root: Path, tmp_path: Path,
) -> None:
    """A7: a symlink placed in pending/ is dropped on lease and the
    next real file is returned. The marker log records the rejection."""
    # Adversarial symlink → arbitrary file outside the queue.
    target_outside = tmp_path / "outside" / "secret.txt"
    target_outside.parent.mkdir()
    target_outside.write_text("attacker_data", encoding="utf-8")

    pending = queue_root / "pending"
    bad = pending / "bad_input.mp4"
    os.symlink(target_outside, bad)

    # And one legitimate file that should be returned by the next lease.
    good = pending / "good_input.mp4"
    good.write_bytes(b"OK")

    q = FileQueue(queue_root, host="testhost")
    leased = q.lease()

    # Symlink is gone; good file is returned.
    assert not bad.exists() and not bad.is_symlink()
    assert leased is not None
    assert leased.name == "good_input.mp4"

    # Marker log records the rejection.
    marker = queue_root / "in_progress" / ".rejected_symlinks.log"
    assert marker.exists()
    assert "bad_input.mp4" in marker.read_text(encoding="utf-8")


@needs_symlink
def test_lease_does_not_follow_symlink_to_corrupt_host_dir(
    queue_root: Path, tmp_path: Path,
) -> None:
    """Even if the symlink's target is a sibling under queue_root,
    reject — the contract is "no symlinks, period"."""
    pending = queue_root / "pending"
    pending_target = pending / "real.mp4"
    pending_target.write_bytes(b"OK")

    sneaky = pending / "sneaky.mp4"
    os.symlink(pending_target, sneaky)

    q = FileQueue(queue_root, host="testhost")
    # First lease returns either the real or the sneaky (sorted name
    # order — "real.mp4" < "sneaky.mp4"). Real first.
    first = q.lease()
    assert first is not None
    assert first.name == "real.mp4"

    # Second lease attempts sneaky, gets rejected, returns None.
    second = q.lease()
    assert second is None
    assert not sneaky.exists()


# -------------------------------------------------------------------- A8

def test_reap_rechecks_heartbeat_mid_loop(
    queue_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A8: if .alive is touched between the top-of-loop check and the
    per-file rename loop, bail out — the host is recovering."""
    host_dir = queue_root / "in_progress" / "racehost"
    host_dir.mkdir(parents=True)
    alive = queue_root / "in_progress" / "racehost.alive"
    alive.touch()
    old = time.time() - 3600
    os.utime(alive, (old, old))

    work_file = host_dir / "work.mp4"
    work_file.write_bytes(b"in_progress")
    os.utime(work_file, (old, old))

    # Simulate the host racing back to life: touch alive right after
    # the first check returns. We intercept ``time.time`` calls inside
    # the module so the second invocation reflects "now" and the
    # heartbeat became fresh in between.
    original_stat = Path.stat
    call_count = {"n": 0}

    def fake_stat(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == alive:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                # Second + later stat: host woke up, touched alive.
                alive.touch()
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    q = FileQueue(queue_root, host="reaper")
    count = q.reap_stale(stale_sec=300)

    # The mid-loop recheck saw a fresh heartbeat → no files moved.
    assert count == 0
    assert work_file.exists()


# -------------------------------------------------------------------- 5.2

@pytest.mark.parametrize("raw,expected", [
    ("normalhost", "normalhost"),
    ("host.with.dots", "host.with.dots"),
    ("..\\evil", "___evil"),      # backslash + .. both neutralised
    ("../../escape", "______escape"),  # both `/`s and both `..`s neutralised
    ("a" * 200, "a" * 64),         # length cap
    ("", "unknown"),
    ("   ", "unknown"),
    ("/abs/path", "_abs_path"),
    ("with\x00null", "with_null"),
])
def test_safe_host_name(raw: str, expected: str) -> None:
    assert _safe_host_name(raw) == expected


def test_queue_uses_sanitised_hostname(queue_root: Path) -> None:
    """FileQueue must sanitise its host before using it as a path
    component — a hostile ``host=`` kwarg cannot escape the layout."""
    q = FileQueue(queue_root, host="../../evil")
    # Path is contained within in_progress/.
    assert q.host_dir.parent.resolve() == (queue_root / "in_progress").resolve()
    # The host string itself was scrubbed.
    assert "/" not in q.host
    assert ".." not in q.host
