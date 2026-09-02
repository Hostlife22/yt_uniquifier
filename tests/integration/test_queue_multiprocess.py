"""Single-host multi-process qualification for shared-filesystem leasing."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from queue import Empty

import pytest

from yt_uniquifier.core.queue.leasing import FileQueue, init_queue


def _drain_queue(root: str, results: multiprocessing.Queue) -> None:
    queue = FileQueue(Path(root))
    names: list[str] = []
    while True:
        leased = queue.lease()
        if leased is None:
            break
        names.append(leased.name)
        queue.release_done(leased)
    results.put(names)


@pytest.mark.integration
def test_four_processes_lease_every_file_exactly_once(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    init_queue(root)
    expected = {f"clip-{index:03d}.mp4" for index in range(80)}
    for name in expected:
        (root / "pending" / name).write_bytes(name.encode("ascii"))

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_drain_queue, args=(str(root), results))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    batches: list[list[str]] = []
    for _ in processes:
        try:
            batches.append(results.get(timeout=5))
        except Empty:
            pytest.fail("worker exited without returning its lease batch")
    leased = [name for batch in batches for name in batch]

    assert set(leased) == expected
    assert len(leased) == len(set(leased)) == len(expected)
    assert FileQueue(root).stats() == {
        "pending": 0,
        "in_progress": 0,
        "done": len(expected),
        "failed": 0,
    }
