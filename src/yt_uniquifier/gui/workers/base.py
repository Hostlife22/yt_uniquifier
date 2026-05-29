"""Common base class for all GUI workers.

Subclasses override `run()` and may add their own signals (e.g.
CalibrateWorker.step(dict), BatchWorker.file_done(str)). Five core
signals are inherited from WorkerBase and provide the lingua franca
that screens connect to without knowing the worker's specifics.
"""

from __future__ import annotations

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyQt6 is not installed. Install: pip install 'yt-uniquifier[gui]'"
    ) from exc

from yt_uniquifier.core.runner import CancelToken


class WorkerBase(QThread):
    """Cancellable QThread emitting typed Qt signals.

    Inherited signals (subclasses are free to override types if more
    specific payloads are needed, e.g. RunWorker overrides finished_ok
    to emit (str, str) instead of (object)).
    """

    started_ = pyqtSignal()                  # underscore avoids QThread.started clash
    finished_ok = pyqtSignal(object)         # result payload (dict / path / etc.)
    failed = pyqtSignal(str)                 # error message
    log = pyqtSignal(str)                    # log line
    progress = pyqtSignal(float, str)        # fraction [0..1], status message

    def __init__(self) -> None:
        super().__init__()
        self.cancel_token = CancelToken()

    def request_cancel(self) -> None:
        """Cooperative cancel — workers check `self.cancel_token.is_cancelled()`."""
        self.cancel_token.cancel()
