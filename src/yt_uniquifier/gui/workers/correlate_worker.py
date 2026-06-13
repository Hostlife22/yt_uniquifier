"""Background runner for tools/validation_correlate.py.

Wraps a ``subprocess.run(..., timeout=60)`` invocation so the Validation
screen's "Run correlation analysis" button does not block the GUI event
loop while the analysis script runs.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.gui.workers.base import WorkerBase


class CorrelateWorker(WorkerBase):
    """Async runner for the validation correlation analysis script.

    A6 (v0.5.5): switched from blocking ``subprocess.run`` to a
    polling Popen pattern so ``request_cancel()`` actually terminates
    the child. The previous implementation set the cancel flag but
    kept the script running to the 60-second timeout.

    Emits:
        correlated(str): stdout from the script on success.
        failed(str):     human-readable error tail on non-zero exit or timeout.
    """

    correlated = pyqtSignal(str)  # stdout
    timeout_sec: float = 60.0
    _poll_interval_sec: float = 0.2

    def __init__(self, script_path: Path, csv_path: Path) -> None:
        super().__init__()
        self.script_path = script_path
        self.csv_path = csv_path

    def _terminate(self, proc: subprocess.Popen[str]) -> None:
        """SIGINT → 5 s grace → SIGKILL, matching runner._terminate."""
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                [sys.executable, str(self.script_path), str(self.csv_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:  # noqa: BLE001 - top-level handler
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        deadline = time.monotonic() + self.timeout_sec
        try:
            while True:
                if self.cancel_token.wait(self._poll_interval_sec):
                    self._terminate(proc)
                    self.failed.emit("correlate cancelled by user")
                    return
                if proc.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    self._terminate(proc)
                    self.failed.emit(
                        f"correlate timed out (>{int(self.timeout_sec)}s)",
                    )
                    return
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
            self.failed.emit("correlate child hung during final drain")
            return
        except Exception as exc:  # noqa: BLE001 - top-level handler
            self._terminate(proc)
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        if proc.returncode != 0:
            tail = (stderr or "").strip()[-1000:]
            self.failed.emit(f"correlate FAILED:\n{tail}")
            return

        self.correlated.emit(stdout)
        self.finished_ok.emit(stdout)
