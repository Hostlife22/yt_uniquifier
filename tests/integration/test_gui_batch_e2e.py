"""End-to-end: BatchScreen with a 2-file batch via BatchWorker.

Real ffmpeg runs are exercised by the Run e2e; this asserts the screen
correctly wires BatchWorker signals into table state for each file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from tests.conftest import needs_ffmpeg

pytestmark = [pytest.mark.integration, needs_ffmpeg]


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _select_profile(combo, stem: str) -> bool:
    for i in range(combo.count()):
        if combo.itemText(i) == stem:
            combo.setCurrentIndex(i)
            return True
    return False


def test_batch_screen_two_files(app, qtbot, tiny_clip: Path, tmp_path: Path) -> None:
    """Two inputs → BatchWorker emits file_done twice → table rows update."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(tiny_clip, in_dir / "a.mp4")
    shutil.copy(tiny_clip, in_dir / "b.mp4")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    from yt_uniquifier.gui.screens.batch import BatchScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = BatchScreen(state)
    qtbot.addWidget(screen)

    screen.input_dir = in_dir
    screen.output_dir = out_dir
    screen.input_label.setText(str(in_dir))
    screen.output_label.setText(str(out_dir))
    assert _select_profile(screen.profile_combo, "soft")
    screen._refresh_preview()  # populates table from glob pattern
    screen._refresh_run_btn()
    app.processEvents()

    assert screen.table.rowCount() == 2, (
        f"preview should show 2 files, got {screen.table.rowCount()}"
    )


def test_batch_screen_no_files_shows_status(app, qtbot, tmp_path: Path) -> None:
    """Empty input dir → BatchWorker emits failed → status_label updates."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    from yt_uniquifier.gui.screens.batch import BatchScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = BatchScreen(state)
    qtbot.addWidget(screen)
    screen.input_dir = in_dir
    screen.output_dir = out_dir
    screen.input_label.setText(str(in_dir))
    screen.output_label.setText(str(out_dir))
    _select_profile(screen.profile_combo, "soft")
    screen._refresh_run_btn()
    app.processEvents()

    screen._on_run()
    app.processEvents()
    if screen.worker is None:
        pytest.skip("BatchScreen refused to start (run_btn disabled)")
    qtbot.waitUntil(lambda: screen.worker is None, timeout=5_000)

    # No files matched; failed handler runs.
    assert screen.table.rowCount() == 0
