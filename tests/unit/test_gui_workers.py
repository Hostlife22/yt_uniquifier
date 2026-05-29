"""WorkerBase + RunWorker + ProbeWorker smoke."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
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


def test_run_worker_cancel_does_not_report_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: user-initiated cancel must not surface as 'failed'.

    Orchestrator raises PipelineError("cancelled by user") when the
    cancel_token is set. The previous RunWorker.run() lumped that into
    the failed branch — UI showed "Failed.", history recorded the run
    as failed. This test pins the corrected behaviour: cancelled signal
    fires, failed does not, and history records status='cancelled'.
    """
    from yt_uniquifier.core.errors import PipelineError
    from yt_uniquifier.gui.workers import run_worker as run_worker_mod
    from yt_uniquifier.gui.workers.run_worker import RunWorker

    history: list[object] = []
    state = MagicMock()
    state.push_history.side_effect = lambda entry: history.append(entry)

    plan = MagicMock()
    plan.source.duration_sec = 60.0
    plan.source.path = tmp_path / "src.mp4"
    plan.profile.name = "soft"
    plan.encoder.name = "libx264"
    plan.plan_hash = "deadbeef"

    options = MagicMock()
    options.output = tmp_path / "out.mp4"

    def _fake_run_full(_plan: object, _opts: object, **kwargs: object) -> object:
        token = kwargs["cancel_token"]
        token.cancel()  # type: ignore[union-attr]
        raise PipelineError("cancelled by user")

    monkeypatch.setattr(run_worker_mod, "run_full", _fake_run_full)

    worker = RunWorker(plan, options, run_qa=False, state=state)
    cancelled_fired: list[bool] = []
    failed_fired: list[str] = []
    worker.cancelled.connect(lambda: cancelled_fired.append(True))
    worker.failed.connect(lambda msg: failed_fired.append(msg))

    # Invoke the run() body synchronously (don't .start() — would need
    # an event-loop pump for signal delivery in a real QThread).
    worker.run()

    assert cancelled_fired == [True], "cancelled signal must fire on user cancel"
    assert failed_fired == [], f"failed signal must NOT fire on cancel: {failed_fired}"
    assert len(history) == 1
    assert history[0].status == "cancelled"  # type: ignore[attr-defined]
