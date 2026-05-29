"""ProfileEditor — load / edit / save roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.gui.screens.profile_editor import ProfileEditorScreen
from yt_uniquifier.gui.state import AppState


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


def test_profile_editor_loads_cid_aware(app: QApplication, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    state = AppState()
    editor = ProfileEditorScreen(state)
    # Select cid_aware if present.
    idx = editor.profile_combo.findText("cid_aware")
    assert idx >= 0
    editor.profile_combo.setCurrentIndex(idx)
    assert editor.current_profile is not None
    assert editor.current_profile.name == "cid_aware"
    assert editor.table.rowCount() > 0


def test_profile_editor_save_as_writes_yaml(app: QApplication, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    state = AppState()
    editor = ProfileEditorScreen(state)
    idx = editor.profile_combo.findText("cid_aware")
    if idx >= 0:
        editor.profile_combo.setCurrentIndex(idx)
    # Simulate collecting the profile + dumping directly (skip QFileDialog).
    out = tmp_path / "my.yaml"
    from yt_uniquifier.core.profile_loader import dump_profile, load_profile
    prof = editor._collect_profile()
    assert prof is not None
    dump_profile(prof, out)
    # Reload from disk → values intact.
    reloaded = load_profile(out)
    assert reloaded.name == prof.name
    assert len(reloaded.transforms) == len(prof.transforms)


def test_profile_editor_invalid_json_rejected(app: QApplication, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Putting invalid JSON in params cell returns None from _collect_profile."""
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr("yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "h.json")
    # Suppress message box.
    monkeypatch.setattr(
        "yt_uniquifier.gui.screens.profile_editor.QMessageBox.critical",
        lambda *a, **kw: None,
    )
    state = AppState()
    editor = ProfileEditorScreen(state)
    # Trash the first row's params cell.
    from PyQt6.QtWidgets import QTableWidgetItem
    editor.table.setItem(0, 2, QTableWidgetItem("{NOT_JSON"))
    assert editor._collect_profile() is None
