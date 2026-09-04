"""v0.7.0 R2 / E6 — global sys.excepthook regression.

The excepthook must:
  1. Write the traceback to `CONFIG_DIR/crash.log`.
  2. Restore the user's stderr trail (delegates to `sys.__excepthook__`).
  3. NOT pop a dialog for `KeyboardInterrupt` — Ctrl+C must stay quiet.
  4. NOT itself raise: a failure inside the hook would be doubly fatal.
  5. Rotate `crash.log` once it exceeds 100 KiB to bound disk usage.

Dialog rendering is intentionally NOT asserted (offscreen QApplication
can't display a modal). The hook short-circuits cleanly when no
QApplication exists or the active Qt platform is headless.
"""

from __future__ import annotations

import sys

import pytest

from yt_uniquifier.gui import app_pyqt


def test_gui_version_option_exits_before_qapplication(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["yt-uniq-gui", "--version"])

    app_pyqt.main()

    assert capsys.readouterr().out.strip() == app_pyqt.__version__


@pytest.fixture()
def isolated_crash_log(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR so crash.log lands under tmp_path."""
    monkeypatch.setattr(app_pyqt, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    yield tmp_path


@pytest.fixture()
def restore_excepthook():
    """Detach pytest-qt's excepthook for the test, restore after.

    pytest-qt installs an excepthook that records any captured
    exception and fails the test at teardown ("Exceptions caught in
    Qt event loop"). Our hook delegates to the prior `sys.excepthook`
    so the stderr path still works — but in tests that prior hook is
    pytest-qt's, so the relay re-arms the failure capture. Swapping
    in `sys.__excepthook__` for the test exercises the same code
    path with a pure-Python upstream.
    """
    saved = sys.excepthook
    sys.excepthook = sys.__excepthook__
    yield
    sys.excepthook = saved


def _raise_and_capture(hook, exc_obj: BaseException | None = None):
    """Synthesize a traceback inside this helper and feed it to `hook`.

    The exception is created + traceback-attached inside a local try
    block, which keeps it confined to this stack frame. pytest-qt's
    exception-capture mechanism only fires for exceptions that escape
    a slot — by handing the tuple directly to `hook`, we exercise the
    excepthook path without raising into the test runner.
    """
    if exc_obj is None:
        exc_obj = RuntimeError("boom-x")
    try:
        raise exc_obj
    except BaseException as captured:  # noqa: BLE001 — synthetic re-raise
        tb = captured.__traceback__
    hook(type(exc_obj), exc_obj, tb)


def test_excepthook_writes_crash_log(isolated_crash_log, restore_excepthook):
    app_pyqt._install_global_excepthook()
    _raise_and_capture(sys.excepthook)
    crash = isolated_crash_log / "crash.log"
    assert crash.exists()
    body = crash.read_text(encoding="utf-8")
    assert "RuntimeError" in body and "boom-x" in body
    assert "---" in body  # timestamp separator


def test_excepthook_appends_not_overwrites(isolated_crash_log, restore_excepthook):
    app_pyqt._install_global_excepthook()
    _raise_and_capture(sys.excepthook)
    _raise_and_capture(sys.excepthook)
    body = (isolated_crash_log / "crash.log").read_text(encoding="utf-8")
    assert body.count("boom-x") == 2


def test_excepthook_rotates_when_oversized(isolated_crash_log, restore_excepthook):
    """A pre-existing >100 KiB crash.log must be rotated to crash.log.1."""
    crash = isolated_crash_log / "crash.log"
    crash.write_text("x" * (101 * 1024), encoding="utf-8")
    app_pyqt._install_global_excepthook()
    _raise_and_capture(sys.excepthook)
    # Old contents moved aside.
    assert (isolated_crash_log / "crash.log.1").exists()
    # New crash.log contains only the fresh entry, not the 101k of 'x'.
    body = crash.read_text(encoding="utf-8")
    assert "RuntimeError" in body
    assert len(body) < 50 * 1024


def test_excepthook_ignores_keyboard_interrupt(isolated_crash_log, restore_excepthook):
    """Ctrl+C must not pop dialogs or write crash.log entries."""
    app_pyqt._install_global_excepthook()
    _raise_and_capture(sys.excepthook, KeyboardInterrupt())
    crash = isolated_crash_log / "crash.log"
    assert not crash.exists()


def test_excepthook_swallows_internal_errors(
    isolated_crash_log, restore_excepthook, monkeypatch,
):
    """If crash.log path is unwritable, the hook must NOT raise."""
    # Replace CONFIG_DIR with a Path that fails on mkdir
    bad_path = isolated_crash_log / "nope"
    bad_path.write_text("not a dir")  # mkdir on a file fails with NotADirectoryError
    monkeypatch.setattr(app_pyqt, "CONFIG_DIR", bad_path)
    app_pyqt._install_global_excepthook()
    # Must not raise even though crash.log cannot be written.
    _raise_and_capture(sys.excepthook)


def test_crash_log_path_helper(isolated_crash_log):
    p = app_pyqt._crash_log_path()
    assert p == isolated_crash_log / "crash.log"
