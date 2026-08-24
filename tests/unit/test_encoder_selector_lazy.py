"""Regression: EncoderSelector must NOT call detect_encoders() synchronously.

The constructor previously invoked ``detect_encoders()`` directly on the
GUI thread, which spawns one ffmpeg subprocess per candidate (~15 s each
on a cold cache). The window froze before its first paint. The current
contract is: render "auto" immediately, then run detection in a
background worker and append items via a signal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.gui.widgets import encoder_selector as encoder_selector_mod
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


def test_encoder_selector_starts_worker_not_synchronous_probe(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[object] = []

    class _RecordingWorker:
        def __init__(self) -> None:
            self.detected = MagicMock()
            self.failed = MagicMock()

        def start(self) -> None:
            started.append(self)

    monkeypatch.setattr(
        encoder_selector_mod, "EncoderDetectWorker", _RecordingWorker,
    )

    sel = EncoderSelector()
    # 'auto' must be rendered immediately (synchronously in __init__).
    assert sel.count() == 1
    assert sel.itemText(0) == "auto"
    # Detection must run on a background worker, not block __init__.
    assert len(started) == 1, (
        "EncoderSelector must hand detection to an EncoderDetectWorker "
        "rather than calling detect_encoders() synchronously"
    )


def test_encoder_selector_appends_detected_items(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeWorker:
        def __init__(self) -> None:
            self.detected = MagicMock()
            self.failed = MagicMock()

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        encoder_selector_mod, "EncoderDetectWorker", _FakeWorker,
    )

    sel = EncoderSelector()
    assert sel.count() == 1  # auto only

    # Simulate the worker's detected signal firing with two candidates.
    cand_ok = MagicMock(
        vendor="x264", codec="h264",
        works=True, max_parallel=4, error=None,
    )
    cand_ok.name = "libx264"  # MagicMock auto-creates .name unless set
    cand_bad = MagicMock(
        vendor="nvenc", codec="h264", works=False,
        max_parallel=1, error="probe failed",
    )
    cand_bad.name = "h264_nvenc"

    sel._on_detected([cand_ok, cand_bad])

    assert sel.count() == 3
    assert "libx264" in sel.itemText(1)
    assert "h264_nvenc" in sel.itemText(2)
    assert "unavailable" in sel.itemText(2)
    assert sel._detect_worker is None


def test_shutdown_preserves_reference_when_worker_is_still_running(
    app: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BusyWorker:
        def __init__(self) -> None:
            self.detected = MagicMock()
            self.failed = MagicMock()
            self.request_cancel = MagicMock()
            self.quit = MagicMock()
            self.wait = MagicMock(return_value=False)

        def start(self) -> None:
            pass

    monkeypatch.setattr(encoder_selector_mod, "EncoderDetectWorker", _BusyWorker)
    sel = EncoderSelector()
    worker = sel._detect_worker

    assert not sel.shutdown_detection(wait_ms=1)
    assert sel._detect_worker is worker
    assert worker is not None
    worker.request_cancel.assert_called_once_with()
    worker.quit.assert_called_once_with()
