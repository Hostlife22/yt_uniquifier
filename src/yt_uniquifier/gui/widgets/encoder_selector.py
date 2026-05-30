"""EncoderSelector — dropdown with detect_encoders() + availability indicator."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QCloseEvent, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QComboBox

from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.encoder_detect_worker import EncoderDetectWorker


class EncoderSelector(QComboBox):
    """Combo populated from detect_encoders() — populated lazily.

    The probe itself can take ~15 s × N candidates on a cold cache (each
    call spawns an ffmpeg test-encode). Running it from ``__init__`` on
    the GUI thread froze the window before the first paint. We render
    ``auto`` immediately and run the actual detection in a background
    worker; detected entries appear when the worker signals.

    - 'auto' option always first (None data).
    - Detected encoders listed in order.
    - Unavailable (``works=False``) entries disabled with tooltip.
    - Emits ``encoder_changed(object)`` — str (encoder name) or None for 'auto'.
    """

    encoder_changed = pyqtSignal(object)             # str | None

    def __init__(self, state: AppState | None = None) -> None:
        super().__init__()
        self.state = state
        self._model = QStandardItemModel()
        self.setModel(self._model)
        self._detect_worker: EncoderDetectWorker | None = None
        self._populate_auto_only()
        self.currentIndexChanged.connect(self._on_changed)
        self._start_detection()

    def _populate_auto_only(self) -> None:
        auto = QStandardItem("auto")
        auto.setData(None)
        self._model.appendRow(auto)

    def _start_detection(self) -> None:
        """Kick off detection on a background thread.

        Cached cases return quickly; cold-start may take several
        seconds without blocking the UI.
        """
        self._detect_worker = EncoderDetectWorker()
        self._detect_worker.detected.connect(self._on_detected)
        # Detection failure is silent — the selector stays as "auto" only,
        # mirroring the previous behaviour on YtUniquifierError.
        self._detect_worker.failed.connect(lambda _msg: self._clear_worker())
        self._detect_worker.start()

    def _on_detected(self, candidates: object) -> None:
        if not isinstance(candidates, list):
            self._clear_worker()
            return
        for cand in candidates:
            label = f"{cand.name}  ({cand.vendor})"
            if not cand.works:
                label += "  — unavailable"
            item = QStandardItem(label)
            item.setData(cand.name if cand.works else None)
            if not cand.works:
                item.setEnabled(False)
                item.setToolTip(
                    f"Detected but failed test-run: {cand.error or '(no error msg)'}",
                )
            else:
                item.setToolTip(
                    f"max parallel: {cand.max_parallel}  ·  codec: {cand.codec}",
                )
            self._model.appendRow(item)
        self._clear_worker()

    def _clear_worker(self) -> None:
        """Join the EncoderDetectWorker QThread before dropping the ref.

        Cold-start detection can take several seconds. If the user closes
        the window mid-probe, dropping the Python ref while the C++
        QThread is still in run() leads to "QThread: Destroyed while
        thread is still running". EncoderSelector lives inside multiple
        screens (Run, Batch, Queue, Validation) and the screens'
        closeEvent walker doesn't see this attribute, so it has to clean
        up its own worker.
        """
        if self._detect_worker is None:
            return
        # Tests sometimes substitute a plain placeholder for the worker
        # (e.g. to assert `_clear_worker` is reached after `failed`);
        # only call quit/wait on a real QThread.
        if hasattr(self._detect_worker, "quit"):
            self._detect_worker.quit()
        if hasattr(self._detect_worker, "wait"):
            self._detect_worker.wait(1000)
        self._detect_worker = None

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Ensure the detect worker is joined when the parent window closes."""
        self._clear_worker()
        super().closeEvent(event)

    def _on_changed(self, idx: int) -> None:
        item = self._model.item(idx)
        if item is None:
            return
        name = item.data()
        self.encoder_changed.emit(name)
