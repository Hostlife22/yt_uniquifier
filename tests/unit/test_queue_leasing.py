"""FileQueue + leasing primitives — atomic rename + heartbeat + reaper."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.queue.leasing import (
    FileQueue,
    QueueError,
    init_queue,
    queue_layout,
)

# ---- layout + init --------------------------------------------------------

def test_init_creates_four_subdirs(tmp_path: Path) -> None:
    layout = init_queue(tmp_path / "q")
    for d in (layout.pending, layout.in_progress, layout.done, layout.failed):
        assert d.is_dir()


def test_init_verifies_atomic_rename(tmp_path: Path) -> None:
    """init's probe file must be cleaned up regardless of outcome."""
    init_queue(tmp_path / "q")
    assert not (tmp_path / "q" / ".rename_probe_src").exists()
    assert not (tmp_path / "q" / "pending" / ".rename_probe_dst").exists()


def test_init_raises_when_rename_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core.queue import leasing as leasing_mod

    def fake_rename(*_a: object, **_kw: object) -> None:
        raise OSError("Operation not supported")

    monkeypatch.setattr(leasing_mod.os, "rename", fake_rename)
    with pytest.raises((QueueError, YtUniquifierError), match="atomic"):
        init_queue(tmp_path / "q")


def test_queue_layout_paths(tmp_path: Path) -> None:
    layout = queue_layout(tmp_path)
    assert layout.pending == tmp_path / "pending"
    assert layout.in_progress == tmp_path / "in_progress"
    assert layout.done == tmp_path / "done"
    assert layout.failed == tmp_path / "failed"


# ---- producer (add) -------------------------------------------------------

def test_add_hardlinks_file(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"fake mp4")
    q = FileQueue(tmp_path / "q", host="hostA")
    dest = q.add(src)
    assert dest.exists()
    assert dest.parent.name == "pending"
    # Source still exists (hard link, not move).
    assert src.exists()


def test_add_raises_when_already_queued(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x")
    q = FileQueue(tmp_path / "q", host="hostA")
    q.add(src)
    with pytest.raises(FileExistsError):
        q.add(src)


def test_add_missing_file_raises(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    q = FileQueue(tmp_path / "q")
    with pytest.raises(FileNotFoundError):
        q.add(tmp_path / "nonexistent.mp4")


# ---- consumer (lease/release) ---------------------------------------------

def _seed_pending(layout_root: Path, names: list[str]) -> None:
    for n in names:
        (layout_root / "pending" / n).write_bytes(b"x")


def test_lease_empty_returns_none(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    q = FileQueue(tmp_path / "q", host="hostA")
    assert q.lease() is None


def test_implicit_worker_identity_is_unique_per_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GitHub's macOS arm64 runner can report a hostname longer than the queue's
    # 64-character component policy. The PID+nonce must survive truncation.
    monkeypatch.setattr(
        "yt_uniquifier.core.queue.leasing.socket.gethostname",
        lambda: "cloud-runner-" + "a" * 100,
    )
    init_queue(tmp_path / "q")
    first = FileQueue(tmp_path / "q")
    second = FileQueue(tmp_path / "q")

    assert first.host == second.host
    assert len(first.worker_id) <= 64
    assert len(second.worker_id) <= 64
    assert first.worker_id != second.worker_id
    assert first.host_dir != second.host_dir
    first.heartbeat()
    second.heartbeat()
    assert (first.layout.in_progress / f"{first.worker_id}.alive").exists()
    assert (second.layout.in_progress / f"{second.worker_id}.alive").exists()


def test_lease_moves_to_host_dir(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    assert leased.parent == tmp_path / "q" / "in_progress" / "hostA"
    assert leased.name == "a.mp4"
    assert not (tmp_path / "q" / "pending" / "a.mp4").exists()


def test_lease_skips_dotfiles(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    (tmp_path / "q" / "pending" / ".hidden").write_bytes(b"x")
    q = FileQueue(tmp_path / "q")
    assert q.lease() is None


def test_two_workers_no_double_lease(tmp_path: Path) -> None:
    """Two threads racing on the same pending file: exactly one wins."""
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", [f"f{i}.mp4" for i in range(20)])
    qa = FileQueue(tmp_path / "q", host="A")
    qb = FileQueue(tmp_path / "q", host="B")

    leased_a: list[Path] = []
    leased_b: list[Path] = []

    def drain(q: FileQueue, store: list[Path]) -> None:
        while True:
            leased = q.lease()
            if leased is None:
                return
            store.append(leased)

    t1 = threading.Thread(target=drain, args=(qa, leased_a))
    t2 = threading.Thread(target=drain, args=(qb, leased_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    names_a = {p.name for p in leased_a}
    names_b = {p.name for p in leased_b}
    # No file leased twice.
    assert names_a.isdisjoint(names_b)
    # All 20 files accounted for.
    assert len(names_a | names_b) == 20


def test_release_done_moves_to_done(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    dest = q.release_done(leased)
    assert dest == tmp_path / "q" / "done" / "a.mp4"
    assert dest.exists()


def test_release_failed_writes_err(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    dest = q.release_failed(leased, "Traceback:\nfoo")
    assert dest == tmp_path / "q" / "failed" / "hostA" / "a.mp4"
    err = tmp_path / "q" / "failed" / "hostA" / "a.mp4.err.txt"
    assert err.read_text() == "Traceback:\nfoo"


def test_commit_output_fences_then_atomically_publishes(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    output = tmp_path / "out" / "a.uniq.mp4"
    output.parent.mkdir()
    staged = q.staged_output_path(output)
    staged.write_bytes(b"complete output")

    done = q.commit_output(leased, staged, output)

    assert done == tmp_path / "q" / "done" / "a.mp4"
    assert done.exists()
    assert output.read_bytes() == b"complete output"
    assert not staged.exists()


def test_commit_output_rejects_a_reaped_lease(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    worker = FileQueue(tmp_path / "q", host="worker")
    leased = worker.lease()
    assert leased is not None
    worker.heartbeat()
    alive = tmp_path / "q" / "in_progress" / "worker.alive"
    old = time.time() - 1_000
    os.utime(alive, (old, old))
    assert FileQueue(tmp_path / "q", host="reaper").reap_stale(stale_sec=300) == 1

    output = tmp_path / "a.uniq.mp4"
    staged = worker.staged_output_path(output)
    staged.write_bytes(b"obsolete output")
    with pytest.raises(QueueError, match="ownership lost"):
        worker.commit_output(leased, staged, output)

    assert not output.exists()
    assert staged.exists()
    assert (tmp_path / "q" / "pending" / "a.mp4").exists()


# ---- heartbeat + reaper ---------------------------------------------------

def test_heartbeat_creates_alive_file(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    q = FileQueue(tmp_path / "q", host="hostA")
    q.heartbeat()
    assert (tmp_path / "q" / "in_progress" / "hostA.alive").exists()


def test_reap_returns_zero_when_no_stale(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    q.heartbeat()  # alive now
    assert q.reap_stale(stale_sec=300) == 0
    # File still in_progress/hostA.
    assert leased.exists()


def test_reap_recovers_stale(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4", "b.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    a = q.lease()
    b = q.lease()
    assert a is not None and b is not None
    # Heartbeat, then backdate the alive file's mtime.
    q.heartbeat()
    alive = tmp_path / "q" / "in_progress" / "hostA.alive"
    long_ago = time.time() - 1000
    os.utime(alive, (long_ago, long_ago))

    # Another worker comes along, reaps, and the files are back in pending.
    qb = FileQueue(tmp_path / "q", host="hostB")
    assert qb.reap_stale(stale_sec=300) == 2
    assert (tmp_path / "q" / "pending" / "a.mp4").exists()
    assert (tmp_path / "q" / "pending" / "b.mp4").exists()
    # Stale alive file removed so the same host can re-heartbeat fresh.
    assert not alive.exists()


def test_reap_ignores_recent_workers(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    q.lease()
    q.heartbeat()
    qb = FileQueue(tmp_path / "q", host="hostB")
    # hostA heartbeated 0 seconds ago — well within stale_sec=300.
    assert qb.reap_stale(stale_sec=300) == 0


# ---- stats ----------------------------------------------------------------

def test_stats_counts(tmp_path: Path) -> None:
    init_queue(tmp_path / "q")
    _seed_pending(tmp_path / "q", ["a.mp4", "b.mp4", "c.mp4", "d.mp4"])
    q = FileQueue(tmp_path / "q", host="hostA")
    leased = q.lease()
    assert leased is not None
    q.release_done(leased)
    failed = q.lease()
    assert failed is not None
    q.release_failed(failed, "oops")

    s = q.stats()
    assert s["pending"] == 2
    assert s["in_progress"] == 0
    assert s["done"] == 1
    assert s["failed"] == 1
