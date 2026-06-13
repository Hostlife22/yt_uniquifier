"""A4 (v0.5.5) regression: CheckpointStore guards work_dir against
concurrent process owners.

Pre-fix two ``yt-uniq batch`` processes that accidentally shared the
same ``--work-dir`` for the same plan would race on the read-modify-
write of ``state.json``. The PID-suffixed tmp filename prevents torn
writes but does NOT prevent last-writer-wins on the final ``state.json``
content: one process flushes a state where the other process's
already-done segments appear as pending, silently losing segment
progress.

Post-fix CheckpointStore acquires a ``<work_dir>/.lock.json`` keyed by
(pid, hostname). A second process on the same host that opens the same
work_dir raises ``CheckpointError`` instead of corrupting state.

Re-entry from the same PID is allowed (resumed runs, tests that create
multiple stores) and orphaned locks (owner PID is dead) are reclaimed
with a logged warning.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from yt_uniquifier.core.checkpoint import (
    LOCK_FILENAME,
    CheckpointStore,
    _pid_alive,
)
from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)


def _make_plan(tmp_path: Path) -> Plan:
    src = tmp_path / "in.mp4"
    src.touch()
    return Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=10.0, size_bytes=1_000,
            video=[VideoStream(
                index=0, codec="h264", width=320, height=180, fps=24.0,
                duration_sec=10.0, pix_fmt="yuv420p",
                color=HDRInfo(is_hdr=False),
            )],
            audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
        ),
        profile=Profile(name="t"),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="cafebabe" * 2,
        run_seed=0,
    )


def test_lock_file_created_on_init(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    store = CheckpointStore(work_dir, plan)

    lock_path = work_dir / LOCK_FILENAME
    assert lock_path.exists(), "init must create the lock file"
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    assert raw["pid"] == os.getpid()
    assert raw["plan_hash"] == plan.plan_hash

    store.close()


def test_same_pid_reentry_is_allowed(tmp_path: Path) -> None:
    """Tests, resumed runs, and re-import scenarios must work within
    one process. Two stores from the same PID on the same work_dir is
    fine — RLock handles in-process concurrency."""
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    s1 = CheckpointStore(work_dir, plan)
    s2 = CheckpointStore(work_dir, plan)  # must not raise
    assert s1._owns_lock and s2._owns_lock
    s2.close()
    s1.close()


def test_orphan_lock_with_dead_pid_is_reclaimed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    # Plant a lock file owned by a guaranteed-dead PID. PID 1 is init
    # which we don't own — so the alive probe under the test user
    # returns True via PermissionError. Use a synthetic huge PID that
    # the kernel won't assign.
    bogus_pid = 2_000_000_001
    assert not _pid_alive(bogus_pid)
    (work_dir / LOCK_FILENAME).write_text(json.dumps({
        "pid": bogus_pid,
        "hostname": "ghosthost",
        "plan_hash": plan.plan_hash,
        "acquired_at": 0.0,
    }), encoding="utf-8")

    with caplog.at_level("WARNING"):
        store = CheckpointStore(work_dir, plan)

    assert "reclaiming stale lock" in caplog.text
    raw = json.loads((work_dir / LOCK_FILENAME).read_text(encoding="utf-8"))
    assert raw["pid"] == os.getpid(), "lock should be ours now"
    store.close()


def test_corrupt_lock_is_overwritten(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / LOCK_FILENAME).write_text("{ not json", encoding="utf-8")

    store = CheckpointStore(work_dir, plan)  # must not raise
    raw = json.loads((work_dir / LOCK_FILENAME).read_text(encoding="utf-8"))
    assert raw["pid"] == os.getpid()
    store.close()


def test_close_releases_lock(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    store = CheckpointStore(work_dir, plan)
    assert (work_dir / LOCK_FILENAME).exists()

    store.close()
    assert not (work_dir / LOCK_FILENAME).exists(), (
        "close() must release the lock"
    )


def test_context_manager_releases_lock(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"

    with CheckpointStore(work_dir, plan):
        assert (work_dir / LOCK_FILENAME).exists()

    assert not (work_dir / LOCK_FILENAME).exists()


def test_concurrent_process_lock_collision(tmp_path: Path) -> None:
    """A4 core regression: spawn a child Python process that tries to
    acquire the same work_dir while this parent holds it. The child
    must raise CheckpointError, not silently overwrite the lock."""
    plan = _make_plan(tmp_path)
    work_dir = tmp_path / "work"
    parent_store = CheckpointStore(work_dir, plan)

    # The child opens a CheckpointStore on the same work_dir. The parent
    # is alive (this test) so the child's _acquire_process_lock must
    # raise CheckpointError. We assert non-zero exit + the expected
    # error class via stderr.
    child_snippet = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        from yt_uniquifier.core.checkpoint import CheckpointStore
        from yt_uniquifier.core.errors import CheckpointError
        from yt_uniquifier.core.models import (
            AudioStream, EncoderCandidate, HDRInfo, Plan,
            Profile, SourceMeta, VideoStream,
        )

        src = Path({str(plan.source.path)!r})
        plan = Plan(
            source=SourceMeta(
                path=src, container="mp4", duration_sec=10.0,
                size_bytes=1_000,
                video=[VideoStream(
                    index=0, codec="h264", width=320, height=180,
                    fps=24.0, duration_sec=10.0, pix_fmt="yuv420p",
                    color=HDRInfo(is_hdr=False),
                )],
                audio=[AudioStream(
                    index=1, codec="aac", sample_rate=48000, channels=2,
                )],
            ),
            profile=Profile(name="t"),
            encoder=EncoderCandidate(
                name="libx264", vendor="x264", codec="h264", works=True,
            ),
            plan_hash={plan.plan_hash!r},
            run_seed=0,
        )
        try:
            CheckpointStore(Path({str(work_dir)!r}), plan)
        except CheckpointError as exc:
            # Expected — parent still owns the lock.
            print(f"GOT_CHECKPOINT_ERROR: {{exc}}")
            sys.exit(42)
        print("UNEXPECTED_SUCCESS")
        sys.exit(0)
    """)
    proc = subprocess.run(
        [sys.executable, "-c", child_snippet],
        capture_output=True, text=True, timeout=30,
        # Ensure the child can import the package even if PYTHONPATH
        # isn't preconfigured (editable install handles this normally).
        env={**os.environ},
    )
    parent_store.close()

    assert proc.returncode == 42, (
        f"child should have raised CheckpointError; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "GOT_CHECKPOINT_ERROR" in proc.stdout
    assert "already in use by PID" in proc.stdout


def test_atexit_cleanup_releases_lock(tmp_path: Path) -> None:
    """A subprocess that creates a store and exits normally must leave
    no lockfile behind — atexit handler does the cleanup."""
    plan_path = tmp_path / "in.mp4"
    plan_path.touch()
    work_dir = tmp_path / "work"

    snippet = textwrap.dedent(f"""
        from pathlib import Path
        from yt_uniquifier.core.checkpoint import CheckpointStore
        from yt_uniquifier.core.models import (
            AudioStream, EncoderCandidate, HDRInfo, Plan,
            Profile, SourceMeta, VideoStream,
        )

        plan = Plan(
            source=SourceMeta(
                path=Path({str(plan_path)!r}), container="mp4",
                duration_sec=10.0, size_bytes=1_000,
                video=[VideoStream(
                    index=0, codec="h264", width=320, height=180,
                    fps=24.0, duration_sec=10.0, pix_fmt="yuv420p",
                    color=HDRInfo(is_hdr=False),
                )],
                audio=[AudioStream(
                    index=1, codec="aac", sample_rate=48000, channels=2,
                )],
            ),
            profile=Profile(name="t"),
            encoder=EncoderCandidate(
                name="libx264", vendor="x264", codec="h264", works=True,
            ),
            plan_hash="ababcdcd" * 2,
            run_seed=0,
        )
        store = CheckpointStore(Path({str(work_dir)!r}), plan)
        # No explicit close — relying on atexit.
    """)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    assert not (work_dir / LOCK_FILENAME).exists(), (
        "atexit handler should have released the lock; "
        "lock file still present after child exit"
    )
