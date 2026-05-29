"""Headless smoke: MainWindow opens + every sidebar entry renders without error."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.mark.smoke
def test_main_window_launches_and_navigates() -> None:
    """Open MainWindow, traverse all 10 sidebar entries, no exceptions."""
    from yt_uniquifier.gui.app_pyqt import SIDEBAR_ITEMS, MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()

    assert win.sidebar.count() == len(SIDEBAR_ITEMS)

    for i in range(win.sidebar.count()):
        win.sidebar.setCurrentRow(i)
        app.processEvents()
        # Stack index follows sidebar selection.
        assert win.stack.currentIndex() == i

    win.close()
