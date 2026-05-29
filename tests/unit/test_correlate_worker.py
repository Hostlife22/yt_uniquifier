"""CorrelateWorker — background runner for validation_correlate.py.

Regression: previously the Validation screen called subprocess.run with
timeout=60 directly from a Qt slot, blocking the GUI event loop for up
to a minute. This test pins the worker contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.gui.workers import correlate_worker as correlate_mod
from yt_uniquifier.gui.workers.correlate_worker import CorrelateWorker


def test_correlate_worker_emits_stdout_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = MagicMock(stdout="r=0.87\np=0.001\n", stderr="", returncode=0)
    monkeypatch.setattr(
        correlate_mod.subprocess, "run", lambda *_a, **_kw: fake_proc,
    )

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    received: list[str] = []
    failed: list[str] = []
    worker.correlated.connect(received.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert received == ["r=0.87\np=0.001\n"]
    assert failed == []


def test_correlate_worker_emits_failed_on_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.CalledProcessError(1, [], "", "boom\n")

    monkeypatch.setattr(correlate_mod.subprocess, "run", _raise)

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "FAILED" in failed[0]
    assert "boom" in failed[0]


def test_correlate_worker_emits_failed_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired([], 60)

    monkeypatch.setattr(correlate_mod.subprocess, "run", _raise)

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "timed out" in failed[0]


def test_validation_screen_on_correlate_does_not_block_gui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ValidationScreen._on_correlate must hand work off to a worker
    instead of calling subprocess inline. The previous implementation
    froze the UI thread for up to 60 seconds."""
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.screens import validation as validation_mod
    from yt_uniquifier.gui.screens.validation import ValidationScreen
    from yt_uniquifier.gui.state import AppState

    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")

    # Make CORRELATE_TOOL and DEFAULT_CSV "exist" so _on_correlate
    # gets past its pre-flight checks.
    tool = tmp_path / "validation_correlate.py"
    tool.touch()
    csv_path = tmp_path / "validation_log.csv"
    csv_path.touch()
    monkeypatch.setattr(validation_mod, "CORRELATE_TOOL", tool)
    monkeypatch.setattr(validation_mod, "DEFAULT_CSV", csv_path)

    # Capture worker construction & start; do not actually start a thread.
    started_workers: list[object] = []

    class _RecordingWorker:
        def __init__(self, script: Path, csv: Path) -> None:
            self.script = script
            self.csv = csv
            self.correlated = MagicMock()
            self.failed = MagicMock()

        def start(self) -> None:
            started_workers.append(self)

    monkeypatch.setattr(validation_mod, "CorrelateWorker", _RecordingWorker)

    _ = QApplication.instance() or QApplication([])
    state = AppState()
    screen = ValidationScreen(state)

    screen._on_correlate()

    assert len(started_workers) == 1, (
        "_on_correlate must construct and start a CorrelateWorker, "
        "not call subprocess.run inline"
    )
    assert screen.corr_worker is started_workers[0]
    assert not screen.run_corr_btn.isEnabled()
