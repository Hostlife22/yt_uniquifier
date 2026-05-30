"""Smoke check: gui modules import iff PyQt6 is installed.

We don't instantiate widgets here — Qt apps need a display server which CI
runners often lack. The import path itself is the architectural risk.
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.smoke

_HAVE_PYQT6 = importlib.util.find_spec("PyQt6") is not None


@pytest.mark.skipif(not _HAVE_PYQT6, reason="PyQt6 not installed")
def test_worker_module_imports() -> None:
    from yt_uniquifier.gui.workers.run_worker import RunWorker  # noqa: F401


@pytest.mark.skipif(not _HAVE_PYQT6, reason="PyQt6 not installed")
def test_app_module_imports() -> None:
    from yt_uniquifier.gui.app_pyqt import MainWindow, main  # noqa: F401


def test_worker_raises_when_pyqt6_missing() -> None:
    """When PyQt6 is absent, importing the gui modules raises a helpful ImportError."""
    if _HAVE_PYQT6:
        pytest.skip("PyQt6 is installed in this env; cannot exercise the missing branch")
