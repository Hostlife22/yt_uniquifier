"""WorkerBase + RunWorker + ProbeWorker smoke."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread

from yt_uniquifier.gui.worker import Worker  # back-compat shim
from yt_uniquifier.gui.workers.base import WorkerBase


def test_workerbase_has_required_signals() -> None:
    """All five core signals must be defined on the base class."""
    for sig in ("started_", "finished_ok", "failed", "log", "progress"):
        assert hasattr(WorkerBase, sig)


def test_workerbase_subclass_can_cancel(tmp_path: Path) -> None:
    class Dummy(WorkerBase):
        def run(self) -> None:
            pass

    w = Dummy()
    assert not w.cancel_token.is_cancelled()
    w.request_cancel()
    assert w.cancel_token.is_cancelled()


def test_workerbase_is_qthread_subclass() -> None:
    assert issubclass(WorkerBase, QThread)


def test_shim_worker_is_run_worker() -> None:
    """Pre-v0.5 import path `gui.worker.Worker` still resolves to RunWorker."""
    from yt_uniquifier.gui.workers.run_worker import RunWorker
    assert Worker is RunWorker
