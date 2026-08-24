"""v0.7.0 R2 / E1 — accessibility regression for the GUI layer.

Walks every screen + widget and asserts that every interactive Qt
widget has a non-empty `accessibleName()`. The class list comes from
`yt_uniquifier.gui.a11y.INTERACTIVE_WIDGET_CLASSES` so the contract
is centralized — expanding it is a deliberate change.

Failures from this test are the screen-reader equivalent of a missing
`alt=` on an `<img>`: the assistive-tech user hears "button" with no
context. Fix by calling `a11y.mark(widget, "Run", ...)`.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

from yt_uniquifier.gui.a11y import INTERACTIVE_WIDGET_CLASSES
from yt_uniquifier.gui.app_pyqt import SIDEBAR_ITEMS, _build_screen
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector


def _stop_background_workers(screen: QWidget) -> None:
    """Join every QThread attached to the screen before it gets GC'd.

    Several screens (Corpus, Queue) start a status-poller QThread inside
    their ``__init__``. If the test function returns while the thread is
    still in ``run()``, Python drops the Python ref while the C++
    QThread is alive — Qt aborts with "QThread: Destroyed while thread
    is still running". Mirror MainWindow.closeEvent's walker so the
    accessibility test never leaks a thread between parametrized cases.
    """
    for selector in screen.findChildren(EncoderSelector):
        assert selector.shutdown_detection(), "encoder detection did not stop"
    for attr_name in dir(screen):
        if attr_name.startswith("__"):
            continue
        try:
            obj = getattr(screen, attr_name)
        except Exception:  # noqa: BLE001 - defensive walker
            continue
        if isinstance(obj, QThread) and obj.isRunning():
            cancel = getattr(obj, "request_cancel", None)
            if callable(cancel):
                cancel()
            obj.quit()
            obj.wait(2000)


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


# Known un-marked widgets that we have not yet covered in the R2 sweep.
# Tracked in plan §Round 2; deliberately enumerated so adding new
# accessibility coverage flips entries off the list with each commit.
#
# Format: dict mapping screen label -> set of "ClassName::object_or_text"
# identifiers. Identifier picks the most stable handle Qt exposes:
# objectName when set, else displayed text, else class+index.
_R2_PENDING: dict[str, set[str]] = {
    # R2 complete: all 10 sidebar screens covered as of R2.2.
    # Leave this dict intact (but empty) so new screens added in v0.8+
    # can be deferred individually rather than re-introducing the
    # global "*" sentinel.
}


# Internal-only Qt composites whose inner controls aren't separately
# user-facing — the wrapper's accessibleName covers them.
_COMPOSITE_PARENT_CLASSES = ("QComboBox", "QSpinBox", "QDoubleSpinBox")


def _interactive_widgets(root: QWidget) -> list[QWidget]:
    """Return every interactive descendant (R2 class allow-list)."""
    result: list[QWidget] = []
    for w in root.findChildren(QWidget):
        if type(w).__name__ not in INTERACTIVE_WIDGET_CLASSES:
            continue
        # Skip widgets hosted *inside* a composite control: e.g.
        # QComboBox's internal QLineEdit + QListView, and the
        # qt_spinbox_lineedit on every QSpinBox / QDoubleSpinBox.
        parent = w.parent()
        is_internal = False
        while parent is not None:
            if type(parent).__name__ in _COMPOSITE_PARENT_CLASSES:
                is_internal = True
                break
            parent = parent.parent()
        if is_internal:
            continue
        result.append(w)
    return result


@pytest.mark.parametrize("label,_lands_in", SIDEBAR_ITEMS)
def test_screen_interactive_widgets_have_accessible_names(
    app: QApplication,
    label: str,
    _lands_in: str,
) -> None:
    """Every QPushButton / QComboBox / QSpinBox etc. has accessibleName."""
    if _R2_PENDING.get(label) == {"*"}:
        pytest.skip(f"{label}: R2 follow-up — full sweep deferred to later commit")

    state = AppState()
    screen = _build_screen(label, _lands_in, state)
    try:
        missing: list[str] = []
        for w in _interactive_widgets(screen):
            if not w.accessibleName():
                cls = type(w).__name__
                text = getattr(w, "text", lambda: "")()
                obj = w.objectName()
                ident = f"{cls}#{obj}" if obj else f"{cls}({text!r})"
                missing.append(ident)

        assert not missing, (
            f"{label}: {len(missing)} interactive widget(s) missing accessibleName. "
            "Wrap construction with a11y.mark(...). Missing: "
            + ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        )
    finally:
        _stop_background_workers(screen)


def test_interactive_class_list_is_non_empty() -> None:
    """Guard against accidental shrinkage of the allow-list."""
    assert "QPushButton" in INTERACTIVE_WIDGET_CLASSES
    assert "QComboBox" in INTERACTIVE_WIDGET_CLASSES
    assert "QLineEdit" in INTERACTIVE_WIDGET_CLASSES


def test_mark_requires_non_empty_name(app: QApplication) -> None:
    from PyQt6.QtWidgets import QPushButton

    from yt_uniquifier.gui.a11y import mark

    btn = QPushButton("ok")
    with pytest.raises(ValueError, match="non-empty name"):
        mark(btn, "")
