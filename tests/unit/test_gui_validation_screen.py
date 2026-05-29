"""ValidationScreen — 3-step wizard navigation + CSV save."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QTableWidgetItem

from yt_uniquifier.gui.screens.validation import ValidationScreen
from yt_uniquifier.gui.state import AppState


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


def test_validation_step_navigation(app: QApplication, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    state = AppState()
    screen = ValidationScreen(state)

    assert screen.stack.currentIndex() == 0
    screen._go_next()
    assert screen.stack.currentIndex() == 1
    screen._go_next()
    assert screen.stack.currentIndex() == 2
    screen._go_next()  # already at last — clamped
    assert screen.stack.currentIndex() == 2
    screen._go_back()
    assert screen.stack.currentIndex() == 1
    screen._go_back()
    screen._go_back()
    assert screen.stack.currentIndex() == 0


def test_validation_save_csv_appends_row(app: QApplication, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    # Redirect DEFAULT_CSV to a temp file.
    csv_path = tmp_path / "validation_log.csv"
    monkeypatch.setattr("yt_uniquifier.gui.screens.validation.DEFAULT_CSV", csv_path)
    # Silence QMessageBox.information.
    monkeypatch.setattr(
        "yt_uniquifier.gui.screens.validation.QMessageBox.information",
        lambda *a, **kw: None,
    )

    state = AppState()
    screen = ValidationScreen(state)
    # Pretend a variant was generated.
    screen.record_table.insertRow(0)
    screen.record_table.setItem(0, 0, QTableWidgetItem("variant_001"))
    screen.record_table.setItem(0, 1, QTableWidgetItem("0.18"))
    screen.record_table.setItem(0, 2, QTableWidgetItem("0.74"))
    screen.record_table.setItem(0, 3, QTableWidgetItem("17.2"))
    screen.record_table.setItem(0, 4, QTableWidgetItem("2026-06-01"))
    screen.record_table.setItem(0, 5, QTableWidgetItem("VIDEO_ID_123"))
    screen.record_table.setItem(0, 6, QTableWidgetItem("no_match"))
    screen.record_table.setItem(0, 7, QTableWidgetItem("first sample"))

    screen._save_csv()

    # Read the CSV back.
    assert csv_path.exists()
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 1
    assert rows[0]["variant_id"] == "variant_001"
    assert rows[0]["match_status"] == "no_match"
    assert rows[0]["youtube_video_id"] == "VIDEO_ID_123"
