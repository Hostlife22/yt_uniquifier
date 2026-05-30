"""Per-screen smoke: each sidebar entry instantiates and exposes its key widgets.

The existing full-launch smoke only asserts sidebar navigation; this file
asserts that every screen actually built the widgets the user is supposed
to interact with. A missing/renamed widget here is a hard regression of
the UI surface.

Headless: relies on QT_QPA_PLATFORM=offscreen (set globally in conftest).
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget


pytestmark = pytest.mark.smoke


SCREEN_INDEX = {
    "Run":            0,
    "Batch":          1,
    "Calibrate":      2,
    "QA Viewer":      3,
    "Profile Editor": 4,
    "History":        5,
    "Corpus":         6,
    "Queue":          7,
    "Validation":     8,
    "Settings":       9,
}

# (sidebar label, screen class attribute names that must be present and non-None)
EXPECTED_WIDGETS: dict[str, list[str]] = {
    "Run":            ["profile_combo", "run_btn", "cancel_btn", "status_label"],
    "Batch":          ["pattern_edit", "profile_combo", "table", "run_btn", "cancel_btn"],
    "Calibrate":      ["profile_combo", "target_spin", "quality_spin", "iter_spin",
                       "run_btn", "cancel_btn", "save_btn"],
    "QA Viewer":      ["tabs", "open_browser_btn", "compute_btn", "cancel_btn"],
    "Profile Editor": ["profile_combo", "save_btn", "save_as_btn", "reload_btn",
                       "table", "seed_combo"],
    "History":        ["filter_edit", "clear_btn", "table"],
    "Corpus":         ["table", "add_btn", "remove_btn", "refresh_btn"],
    "Queue":          ["root_label", "pick_root_btn", "init_btn", "stats_label",
                       "tabs", "add_files_btn", "profile_combo",
                       "start_btn", "stop_btn"],
    "Validation":     ["step_label", "stack", "back_btn", "next_btn",
                       "gen_profile", "gen_n", "gen_btn",
                       "record_table", "save_csv_btn",
                       "run_corr_btn", "corr_output"],
    "Settings":       ["theme_combo", "default_profile_combo", "recents_cap_spin",
                       "history_cap_spin", "reset_enc_btn", "open_logs_btn",
                       "open_config_btn", "save_btn"],
}


@pytest.fixture(scope="module")
def main_window():
    """One MainWindow shared by every screen test in this module."""
    from yt_uniquifier.gui.app_pyqt import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


@pytest.mark.parametrize("label", list(SCREEN_INDEX.keys()))
def test_screen_instantiates_with_required_widgets(main_window, label: str) -> None:
    """Navigate to the screen and assert its declared widgets exist."""
    idx = SCREEN_INDEX[label]
    main_window.sidebar.setCurrentRow(idx)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    assert main_window.stack.currentIndex() == idx, (
        f"Sidebar row {idx} ('{label}') did not switch the stack"
    )

    screen = main_window.stack.currentWidget()
    assert isinstance(screen, QWidget), f"Screen '{label}' is not a QWidget"

    title = screen.findChild(QWidget, "title")
    assert title is not None, f"Screen '{label}' has no widget with objectName='title'"

    for attr in EXPECTED_WIDGETS[label]:
        widget = getattr(screen, attr, None)
        assert widget is not None, (
            f"Screen '{label}' is missing required widget attribute '{attr}'"
        )


@pytest.mark.parametrize("label", list(SCREEN_INDEX.keys()))
def test_screen_round_trip_navigation(main_window, label: str) -> None:
    """Navigate away from the screen and back; no exceptions, no stale state."""
    target = SCREEN_INDEX[label]
    main_window.sidebar.setCurrentRow(target)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    main_window.sidebar.setCurrentRow(0 if target != 0 else 1)
    app.processEvents()
    main_window.sidebar.setCurrentRow(target)
    app.processEvents()

    assert main_window.stack.currentIndex() == target


def test_run_screen_disable_cancel_initially(main_window) -> None:
    """Cancel must be disabled until a run starts — guards against a misclick."""
    main_window.sidebar.setCurrentRow(SCREEN_INDEX["Run"])
    app = QApplication.instance()
    assert app is not None
    app.processEvents()

    screen = main_window.stack.currentWidget()
    cancel = screen.findChild(QPushButton, "cancel")
    assert cancel is not None
    assert cancel.isEnabled() is False
