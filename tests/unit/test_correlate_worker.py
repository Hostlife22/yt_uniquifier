"""CorrelateWorker — background runner for validation_correlate.py.

Regression: previously the Validation screen called subprocess.run with
timeout=60 directly from a Qt slot, blocking the GUI event loop for up
to a minute. This test pins the worker contract.

A6 (v0.5.5): the worker now spawns a polling Popen loop so
``request_cancel()`` actually terminates the child instead of waiting
for the 60-second timeout. Tests below pin the new mechanics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.gui.workers import correlate_worker as correlate_mod
from yt_uniquifier.gui.workers.correlate_worker import CorrelateWorker


class _FakePopen:
    """Drop-in for subprocess.Popen in tests.

    Configurable: ``stdout`` / ``stderr`` returned by communicate(),
    a final returncode, and either an immediate-exit or a
    never-completes (poll() always None) behaviour for the timeout
    path.
    """

    def __init__(
        self, *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        never_exits: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._final_rc = returncode
        self._never_exits = never_exits
        self._terminated = False
        # poll() returns None until the test allows it to "finish".
        self._exited = False

    def poll(self) -> int | None:
        if self._never_exits and not self._terminated:
            return None
        if not self._exited:
            self._exited = True
            return None  # first poll: still running
        return self._final_rc if not self._terminated else -15

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return self._stdout, self._stderr

    @property
    def returncode(self) -> int:
        return self._final_rc if not self._terminated else -15

    def terminate(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self._terminated = True


def test_correlate_worker_emits_stdout_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakePopen(stdout="r=0.87\np=0.001\n", stderr="", returncode=0)

    def _popen_factory(*_a: Any, **_kw: Any) -> _FakePopen:
        return fake

    monkeypatch.setattr(correlate_mod.subprocess, "Popen", _popen_factory)

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    # Make polling fast so the test doesn't sit on the 0.2 s default.
    worker._poll_interval_sec = 0.01
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
    fake = _FakePopen(stdout="", stderr="boom\n", returncode=1)
    monkeypatch.setattr(
        correlate_mod.subprocess, "Popen", lambda *_a, **_kw: fake,
    )

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    worker._poll_interval_sec = 0.01
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "FAILED" in failed[0]
    assert "boom" in failed[0]


def test_correlate_worker_emits_failed_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakePopen(never_exits=True, returncode=0)
    monkeypatch.setattr(
        correlate_mod.subprocess, "Popen", lambda *_a, **_kw: fake,
    )

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    # Force the deadline to elapse on the first poll.
    worker.timeout_sec = 0.001
    worker._poll_interval_sec = 0.005
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "timed out" in failed[0]
    assert fake._terminated, "timeout path must terminate the child"


def test_correlate_worker_cancel_terminates_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A6 regression: request_cancel terminates the child via SIGTERM
    instead of waiting for the 60-second timeout."""
    fake = _FakePopen(never_exits=True, returncode=0)
    monkeypatch.setattr(
        correlate_mod.subprocess, "Popen", lambda *_a, **_kw: fake,
    )

    worker = CorrelateWorker(tmp_path / "script.py", tmp_path / "log.csv")
    worker._poll_interval_sec = 0.01
    failed: list[str] = []
    worker.failed.connect(failed.append)

    # Fire cancel BEFORE run() so the first cancel_token.wait returns True.
    worker.request_cancel()
    worker.run()

    assert len(failed) == 1
    assert "cancelled by user" in failed[0]
    assert fake._terminated, "cancel path must terminate the child"


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
