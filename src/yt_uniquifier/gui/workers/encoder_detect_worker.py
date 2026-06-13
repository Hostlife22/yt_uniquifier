"""Background ffmpeg encoder probe.

``detect_encoders()`` spawns one ffmpeg subprocess per candidate (up to
~15s each on a cold cache) and is therefore unsuitable for calling
from a Qt widget constructor — it would freeze the window before the
first paint. This worker moves the call off the GUI thread so the
selector can render immediately with just the ``auto`` option, then
append detected entries when the probe completes.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.gui.workers.base import WorkerBase


class EncoderDetectWorker(WorkerBase):
    """Run ``core.encoder.detect_encoders()`` off the GUI thread.

    Emits:
        detected(list[EncoderCandidate]): probe result on success.
        failed(str):                       error message on probe failure.
    """

    detected = pyqtSignal(object)  # list[EncoderCandidate]

    def run(self) -> None:
        try:
            # A6 (v0.5.5): the cold-cache probe spawns up to 10 sequential
            # ffmpeg subprocesses. Honour the cancel token between probes.
            candidates = detect_encoders(cancel_token=self.cancel_token)
        except Exception as exc:  # noqa: BLE001 - top-level worker handler
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.detected.emit(candidates)
        self.finished_ok.emit(candidates)
