"""Real process isolation for the registry used by CLI, web and workers.

Only free-space observation is injected; locks, PIDs, spawn and crash are real.
This never fills the developer's filesystem.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from yt_uniquifier.core import resource_budget as budget


def _reserve(root: str, owner: str, ready: Any, release: Any, crash: bool) -> None:
    os.environ["YT_UNIQ_RESOURCE_LOCK_DIR"] = str(Path(root) / "registry")
    with patch.object(
        budget.shutil, "disk_usage", return_value=SimpleNamespace(free=100),
    ):
        try:
            reservation = budget.DiskReservation.acquire(Path(root), owner, 60)
        except budget.InsufficientDiskReservation:
            ready.put((owner, "rejected"))
            return
        ready.put((owner, "acquired"))
        # Queue feeder must finish before os._exit, otherwise evidence is lost.
        ready.close()
        ready.join_thread()
        if crash:
            os._exit(17)
        try:
            if not release.wait(15):
                raise TimeoutError("parent did not release qualification process")
        finally:
            reservation.release()


@pytest.mark.integration
def test_three_processes_share_one_disk_budget(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    processes = [
        context.Process(target=_reserve, args=(str(tmp_path), owner, ready, release, False))
        for owner in ("cli", "web", "worker")
    ]
    try:
        for process in processes:
            process.start()
        outcomes = [ready.get(timeout=15)[1] for _ in processes]
        assert outcomes.count("acquired") == 1
        assert outcomes.count("rejected") == 2
    finally:
        release.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        ready.close()
    assert all(process.exitcode == 0 for process in processes)


@pytest.mark.integration
def test_other_process_reclaims_crashed_local_reservation(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    for owner, crash in (("original-worker", True), ("replacement-worker", False)):
        process = context.Process(
            target=_reserve, args=(str(tmp_path), owner, ready, release, crash),
        )
        process.start()
        try:
            assert ready.get(timeout=15) == (owner, "acquired")
        finally:
            release.set()
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        assert process.exitcode == (17 if crash else 0)
    ready.close()
