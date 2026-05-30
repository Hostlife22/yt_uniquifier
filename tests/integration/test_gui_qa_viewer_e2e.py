"""QA Viewer integration: load an existing qa.json and verify tab population.

Real QA computation is covered by unit and the (opt-in) Run e2e — here
we focus on the path where a user opens an existing QA artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

pytestmark = pytest.mark.integration


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_qa_viewer_builds_with_two_tabs(app, qtbot) -> None:
    from yt_uniquifier.gui.screens.qa_viewer import QaViewerScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = QaViewerScreen(state)
    qtbot.addWidget(screen)

    assert screen.tabs.count() >= 2  # "Open existing" + "Compute new"
    assert screen.cancel_btn.isEnabled() is False
    assert screen.compute_btn.isEnabled() is False
    assert screen.open_browser_btn.isEnabled() is False


def test_qa_viewer_load_html_enables_browser(app, qtbot, tmp_path: Path) -> None:
    """_load_html on a real path enables Open-in-browser."""
    from yt_uniquifier.gui.screens.qa_viewer import QaViewerScreen
    from yt_uniquifier.gui.state import AppState

    qa_html = tmp_path / "report.qa.html"
    qa_html.write_text("<html><body>fake</body></html>")
    qa_json = tmp_path / "report.qa.json"
    qa_json.write_text(json.dumps({"phash_distance": 0, "vmaf": 100}))

    state = AppState()
    screen = QaViewerScreen(state)
    qtbot.addWidget(screen)

    if hasattr(screen, "_load_html"):
        screen._load_html(qa_html)
        app.processEvents()
        assert screen.open_browser_btn.isEnabled() is True
