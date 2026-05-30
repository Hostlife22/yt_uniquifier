"""Queue screen integration: pick root, init metadata, wire buttons."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

pytestmark = pytest.mark.integration


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_queue_screen_buttons_initial_state(app, qtbot) -> None:
    """init/add/start should be disabled until queue root is picked."""
    from yt_uniquifier.gui.screens.queue import QueueScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = QueueScreen(state)
    qtbot.addWidget(screen)

    assert screen.init_btn.isEnabled() is False
    assert screen.add_files_btn.isEnabled() is False
    assert screen.start_btn.isEnabled() is False


def test_queue_screen_pick_root_enables_actions(app, qtbot, tmp_path: Path,
                                                  monkeypatch) -> None:
    """Picking a root via _pick_root enables init/add buttons."""
    from PyQt6.QtWidgets import QFileDialog

    from yt_uniquifier.gui.screens.queue import QueueScreen
    from yt_uniquifier.gui.state import AppState

    queue_root = tmp_path / "queue"
    queue_root.mkdir()

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *_a, **_kw: str(queue_root)),
    )

    state = AppState()
    screen = QueueScreen(state)
    qtbot.addWidget(screen)

    screen._pick_root()
    app.processEvents()

    assert screen.queue_root == queue_root
    assert screen.init_btn.isEnabled() is True
    assert screen.add_files_btn.isEnabled() is True
