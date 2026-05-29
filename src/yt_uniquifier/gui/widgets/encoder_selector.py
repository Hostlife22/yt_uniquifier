"""EncoderSelector — dropdown with detect_encoders() + availability indicator."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QComboBox

from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.gui.state import AppState


class EncoderSelector(QComboBox):
    """Combo populated from detect_encoders().

    - 'auto' option always first (None data).
    - Detected encoders listed in order.
    - Unavailable (`works=False`) entries disabled with tooltip.
    - Emits `encoder_changed(object)` — str (encoder name) or None for 'auto'.
    """

    encoder_changed = pyqtSignal(object)             # str | None

    def __init__(self, state: AppState | None = None) -> None:
        super().__init__()
        self.state = state
        self._model = QStandardItemModel()
        self.setModel(self._model)
        self._populate()
        self.currentIndexChanged.connect(self._on_changed)

    def _populate(self) -> None:
        auto = QStandardItem("auto")
        auto.setData(None)
        self._model.appendRow(auto)

        try:
            candidates = detect_encoders()
        except YtUniquifierError:
            return  # leave only 'auto' on detection failure

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

    def _on_changed(self, idx: int) -> None:
        item = self._model.item(idx)
        if item is None:
            return
        name = item.data()
        self.encoder_changed.emit(name)
