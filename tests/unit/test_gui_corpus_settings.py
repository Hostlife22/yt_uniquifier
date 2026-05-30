"""CorpusWorker + SettingsScreen smoke tests."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.core.qa.corpus import Corpus, CorpusEntry
from yt_uniquifier.gui.screens.corpus import CorpusScreen
from yt_uniquifier.gui.screens.settings import SettingsScreen
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.corpus_worker import CorpusWorker


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppState:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    return AppState()


# ---- CorpusWorker ----
def test_corpus_worker_adds_entry(tmp_path: Path) -> None:
    fake_entry = CorpusEntry(
        id="abc", path=tmp_path / "in.mp4", added_at=time.time(),
        duration_sec=10.0, phash_frames=(1, 2, 3),
        audio_fingerprint=(4, 5), sample_count=3,
    )
    src = tmp_path / "in.mp4"
    src.touch()
    received: list[CorpusEntry] = []
    with patch.object(Corpus, "add", return_value=fake_entry):
        worker = CorpusWorker(Corpus(tmp_path / "corpus_root"), src)
        worker.entry_added.connect(received.append)
        worker.run()
    assert received and received[0].id == "abc"


def test_corpus_worker_failed_on_exception(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.touch()
    errors: list[str] = []
    with patch.object(Corpus, "add", side_effect=RuntimeError("boom")):
        worker = CorpusWorker(Corpus(tmp_path / "corpus_root"), src)
        worker.failed.connect(errors.append)
        worker.run()
    assert errors and "boom" in errors[0]


# ---- SettingsScreen ----
def test_settings_theme_switch_emits_signal(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _isolate(tmp_path, monkeypatch)
    received: list[str] = []
    state.theme_changed.connect(received.append)
    screen = SettingsScreen(state)
    screen.theme_combo.setCurrentText("light")
    assert "light" in received


def test_settings_save_persists_theme(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _isolate(tmp_path, monkeypatch)
    screen = SettingsScreen(state)
    screen.theme_combo.setCurrentText("light")
    screen._on_save()
    # Re-load from disk.
    state2 = AppState()
    assert state2.theme == "light"


# ---- CorpusScreen ----
def test_corpus_screen_table_populates(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _isolate(tmp_path, monkeypatch)
    fake_entry = CorpusEntry(
        id="x", path=tmp_path / "a.mp4", added_at=time.time(),
        duration_sec=12.0, phash_frames=(1,),
        audio_fingerprint=(),
        sample_count=1,
    )
    with patch.object(Corpus, "list_all", return_value=[fake_entry]):
        screen = CorpusScreen(state)
        # _refresh now dispatches a worker; synthesise the resulting
        # `listed` signal so the table populates synchronously for the
        # test without spinning a real QThread + event loop.
        screen._on_listed([fake_entry])
    assert screen.table.rowCount() == 1
