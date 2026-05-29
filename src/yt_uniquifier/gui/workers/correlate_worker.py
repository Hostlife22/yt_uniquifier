"""Background runner for tools/validation_correlate.py.

Wraps a ``subprocess.run(..., timeout=60)`` invocation so the Validation
screen's "Run correlation analysis" button does not block the GUI event
loop while the analysis script runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.gui.workers.base import WorkerBase


class CorrelateWorker(WorkerBase):
    """Async runner for the validation correlation analysis script.

    Emits:
        correlated(str): stdout from the script on success.
        failed(str):     human-readable error tail on non-zero exit or timeout.
    """

    correlated = pyqtSignal(str)  # stdout
    timeout_sec: float = 60.0

    def __init__(self, script_path: Path, csv_path: Path) -> None:
        super().__init__()
        self.script_path = script_path
        self.csv_path = csv_path

    def run(self) -> None:
        try:
            proc = subprocess.run(
                [sys.executable, str(self.script_path), str(self.csv_path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout_sec,
            )
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "").strip()[-1000:]
            self.failed.emit(f"correlate FAILED:\n{tail}")
            return
        except subprocess.TimeoutExpired:
            self.failed.emit(
                f"correlate timed out (>{int(self.timeout_sec)}s)",
            )
            return
        except Exception as exc:  # noqa: BLE001 - top-level handler
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        self.correlated.emit(proc.stdout)
        self.finished_ok.emit(proc.stdout)
