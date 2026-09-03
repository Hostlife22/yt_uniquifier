"""Real-process crash recovery for distributed output publication."""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from yt_uniquifier.core.queue.leasing import FileQueue, init_queue


def _crash_between_fence_and_publish(queue_root: str, output_root: str) -> None:
    from yt_uniquifier.core.queue import leasing as leasing_mod

    queue = FileQueue(Path(queue_root))
    leased = queue.lease()
    if leased is None:
        os._exit(70)
    output_dir = Path(output_root)
    output = output_dir / "movie.uniq.mp4"
    staged = queue.staged_output_path(output)
    staged.write_bytes(b"encoded and validated output")
    real_replace = leasing_mod.os.replace

    def terminate_on_publication(src: Path, dest: Path) -> None:
        if Path(src) == staged and Path(dest) == output:
            os._exit(73)
        real_replace(src, dest)

    leasing_mod.os.replace = terminate_on_publication
    queue.commit_output(leased, staged, output)
    os._exit(71)


@pytest.mark.integration
def test_real_process_exit_after_fence_is_recovered(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    output_root = tmp_path / "output"
    init_queue(queue_root)
    output_root.mkdir()
    (queue_root / "pending" / "movie.mp4").write_bytes(b"source")

    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_between_fence_and_publish,
        args=(str(queue_root), str(output_root)),
    )
    process.start()
    process.join(timeout=30)
    assert process.exitcode == 73
    fences = list((queue_root / "done").glob(".commit-*.fence"))
    assert len(fences) == 1
    assert not (queue_root / "done" / "movie.mp4").exists()

    alive_files = list((queue_root / "in_progress").glob("*.alive"))
    assert len(alive_files) == 1
    old = time.time() - 1_000
    os.utime(alive_files[0], (old, old))

    recovery = FileQueue(queue_root)
    assert recovery.recover_commits(output_root) == 1
    assert (output_root / "movie.uniq.mp4").read_bytes() == (
        b"encoded and validated output"
    )
    assert (queue_root / "done" / "movie.mp4").exists()
    assert not fences[0].exists()
    assert not list((queue_root / ".commits").glob("commit-*.json"))
