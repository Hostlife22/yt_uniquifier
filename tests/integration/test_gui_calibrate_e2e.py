"""Calibrate screen integration: wire-up checks only.

A real calibration loop takes minutes per iteration; we cover signal
wiring and initial UI state. Worker-level behavior is in unit tests.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication


pytestmark = pytest.mark.integration


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_calibrate_screen_builds_and_run_disabled(app, qtbot) -> None:
    from yt_uniquifier.gui.screens.calibrate import CalibrateScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = CalibrateScreen(state)
    qtbot.addWidget(screen)

    assert screen.profile_combo.count() > 0
    assert screen.iter_spin.value() >= 1
    assert screen.save_btn.isEnabled() is False
    # No input picked → run_btn must be disabled.
    assert screen.run_btn.isEnabled() is False


def test_calibrate_screen_completed_enables_save(app, qtbot) -> None:
    """Calling _on_completed with a tuned profile enables save_btn."""
    from yt_uniquifier.core.models import Profile
    from yt_uniquifier.gui.screens.calibrate import CalibrateScreen
    from yt_uniquifier.gui.state import AppState

    state = AppState()
    screen = CalibrateScreen(state)
    qtbot.addWidget(screen)

    screen._on_completed(Profile(name="tuned", transforms=[]))
    app.processEvents()
    assert screen.save_btn.isEnabled() is True
