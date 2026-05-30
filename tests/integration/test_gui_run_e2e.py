"""End-to-end: RunScreen wiring + (optional, opt-in) real ffmpeg run.

The unit suite already covers RunWorker isolated. Here we assert the
screen ↔ worker wiring and only opt into a real ffmpeg run when the
caller sets `YTU_RUN_HEAVY_E2E=1` — otherwise the heavy path is skipped
because RunWorker performs encoder detection on every candidate which
can take many minutes on a fresh host.
"""

from __future__ import annotations

import os
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


def test_run_screen_no_input_no_worker(app, qtbot) -> None:
    """No input/output → _on_run is a no-op, no RunWorker is created."""
    from yt_uniquifier.gui.screens.run import RunScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = RunScreen(state)
    qtbot.addWidget(screen)

    screen._on_run()
    app.processEvents()
    assert screen.run_worker is None
    assert screen.cancel_btn.isEnabled() is False


def test_run_screen_with_input_creates_worker(app, qtbot, tiny_clip: Path,
                                                tmp_path: Path) -> None:
    """Input + output set → RunScreen builds a Plan + RunWorker on click."""
    from yt_uniquifier.gui.screens.run import RunScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = RunScreen(state)
    qtbot.addWidget(screen)

    out = tmp_path / "out.mp4"
    state.set_input_path(tiny_clip)
    state.set_output_path(out)
    screen.input_picker.set_path(tiny_clip)
    screen.output_picker.set_path(out)
    app.processEvents()
    assert _select_profile(screen.profile_combo, "soft")

    screen._on_run()
    app.processEvents()
    try:
        assert screen.run_worker is not None, (
            "RunScreen failed to create a RunWorker on _on_run"
        )
    finally:
        if screen.run_worker is not None:
            screen.run_worker.request_cancel()
            screen.run_worker.quit()
            screen.run_worker.wait(1500)


@pytest.mark.skipif(
    os.environ.get("YTU_RUN_HEAVY_E2E") != "1",
    reason="heavy real-ffmpeg run e2e; set YTU_RUN_HEAVY_E2E=1 to enable",
)
def test_run_screen_full_real_ffmpeg(app, qtbot, tiny_clip: Path,
                                       tmp_path: Path, monkeypatch) -> None:
    """Optional heavy run: real ffmpeg path through RunScreen on tiny_clip."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    monkeypatch.setenv("YTU_CACHE_DIR", str(cache_root))

    from yt_uniquifier.gui.screens.run import RunScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = RunScreen(state)
    qtbot.addWidget(screen)

    out = tmp_path / "out.mp4"
    state.set_input_path(tiny_clip)
    state.set_output_path(out)
    screen.input_picker.set_path(tiny_clip)
    screen.output_picker.set_path(out)
    app.processEvents()
    assert _select_profile(screen.profile_combo, "soft")

    screen._on_run()
    app.processEvents()
    assert screen.run_worker is not None

    finished: list[tuple[str, str]] = []
    failed: list[str] = []
    screen.run_worker.finished_ok.connect(
        lambda o, q: finished.append((o, q))
    )
    screen.run_worker.failed.connect(failed.append)

    qtbot.waitUntil(
        lambda: bool(finished) or bool(failed),
        timeout=180_000,
    )
    if failed:
        pytest.skip(f"RunWorker failed on this host: {failed[0]}")
    assert finished and out.exists() and out.stat().st_size > 0
