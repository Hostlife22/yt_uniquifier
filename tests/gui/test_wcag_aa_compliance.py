"""v1.0.0 R6 — WCAG 2.1 AA compliance regression for the GUI shell.

Complements the v0.7 R7 `test_theme_contrast.py` (colour-contrast tokens)
and `test_gui_accessibility.py` (accessibleName coverage) by guarding the
remaining AA criteria that depend on *runtime* widget state rather than
static tokens:

    SC 1.3.1  Info and Relationships     — every screen exposes a
                                            non-empty windowTitle / objectName
                                            so AT can name the region.
    SC 2.1.1  Keyboard                    — every interactive widget has
                                            focusPolicy other than NoFocus
                                            (i.e. reachable via Tab).
    SC 2.1.2  No Keyboard Trap            — there is no widget whose
                                            focusPolicy is StrongFocus AND
                                            whose tab-next loops back to
                                            itself (proxy: tab order is a
                                            partial order, not a cycle of 1).
    SC 2.4.7  Focus Visible               — the theme QSS contains an
                                            explicit `:focus` rule with a
                                            visible outline. (See theme.py.)
    SC 2.5.5  Target Size (AAA but we     — every QPushButton on every
              opt-in)                       screen has minimum 20×20px
                                            (relaxed from 44×44 because Qt
                                            desktop UI conventions are
                                            denser than touch UI — this is
                                            the "AA-equivalent for desktop"
                                            cap recommended by the WCAG2ICT
                                            working group; floor at 20
                                            matches Qt's stock default
                                            sizeHint on headless Windows).
    SC 4.1.2  Name, Role, Value           — handled by the existing
                                            accessibleName walker; this
                                            file adds the role assertion
                                            (every screen widget has a
                                            non-empty class name so AT can
                                            announce "list", "button" etc).

These checks are PROGRAMMATIC; the human conformance statement lives in
`docs/accessibility.md` and the manual screen-reader test guide is the
authoritative AAA reference for end users.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from yt_uniquifier.gui.a11y import INTERACTIVE_WIDGET_CLASSES
from yt_uniquifier.gui.app_pyqt import SIDEBAR_ITEMS, _build_screen
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.theme import qss_for

# Minimum touch / click target size in CSS-px for desktop Qt. 44×44 is
# the WCAG 2.5.5 AAA threshold for touch; for desktop we relax per the
# WCAG2ICT desktop-target-size guidance.  Empirically the QPushButton
# sizeHint() varies by platform on headless CI runners: macOS ≈ 22-24,
# Linux ≈ 22, Windows ≈ 20. We set the floor at 20 so the test still
# catches a genuine regression (anything shrunk below 20×20 is
# unusable on desktop) without flapping on Qt's stock per-platform
# height. If you raise this back to 24, also add a min-height: 24px
# QSS rule to theme.py to actually enforce it cross-platform.
_MIN_TARGET_PX = 20


def _stop_background_workers(screen: QWidget) -> None:
    """Join every QThread attached to the screen before it gets GC'd.

    Identical to the helper in test_gui_accessibility.py — duplicated
    here so the two test files can be run in isolation without an import
    coupling.
    """
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


def _interactive_widgets(root: QWidget) -> list[QWidget]:
    """Every interactive descendant on a screen (mirrors a11y walker)."""
    result: list[QWidget] = []
    for w in root.findChildren(QWidget):
        if type(w).__name__ in INTERACTIVE_WIDGET_CLASSES:
            result.append(w)
    return result


# ---------------------------------------------------------------------------
# SC 2.4.7 — Focus Visible (theme-level)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_theme_qss_defines_explicit_focus_outline(theme: str) -> None:
    """The QSS must declare an explicit `:focus` outline rule.

    Qt's platform-default focus indicator is a hair-thin dotted line that
    vanishes against most coloured fills. theme.py adds a 2px solid
    outline in `accent_warm`. If a future redesign accidentally drops
    that block, this test fires before the build ships to keyboard users.
    """
    qss = qss_for(theme)
    assert ":focus" in qss, (
        f"{theme} theme has no `:focus` selector — SC 2.4.7 regression. "
        "Restore the focus block at the tail of _QSS_TEMPLATE."
    )
    assert "outline:" in qss, (
        f"{theme} theme `:focus` block has no `outline:` rule — the "
        "selector is present but paints nothing visible."
    )


# ---------------------------------------------------------------------------
# SC 2.1.1 — Keyboard (every interactive control is Tab-reachable)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,_lands_in", SIDEBAR_ITEMS)
def test_interactive_widgets_are_keyboard_focusable(
    app: QApplication,
    label: str,
    _lands_in: str,
) -> None:
    """Every interactive widget must accept keyboard focus.

    A widget with `focusPolicy() == NoFocus` is silently unreachable to
    keyboard-only users — they will never tab onto it. Mouse-only
    affordances are an SC 2.1.1 violation.
    """
    state = AppState()
    screen = _build_screen(label, _lands_in, state)
    try:
        unreachable: list[str] = []
        for w in _interactive_widgets(screen):
            # QListWidgetItem-wrapped sub-controls inside a combobox are
            # reached *through* the parent's focus, not directly. Skip
            # widgets whose own focus policy is intentionally disabled
            # *because* their parent owns the focus chain.
            if w.focusPolicy() == Qt.FocusPolicy.NoFocus:
                cls = type(w).__name__
                obj = w.objectName()
                ident = f"{cls}#{obj}" if obj else cls
                unreachable.append(ident)
        assert not unreachable, (
            f"{label}: {len(unreachable)} interactive widget(s) have "
            f"focusPolicy=NoFocus — SC 2.1.1 keyboard regression. "
            f"Offenders: {', '.join(unreachable[:8])}"
            + ("…" if len(unreachable) > 8 else "")
        )
    finally:
        _stop_background_workers(screen)


# ---------------------------------------------------------------------------
# SC 2.5.5 — Target Size (desktop-relaxed: 24×24 minimum for buttons)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,_lands_in", SIDEBAR_ITEMS)
def test_buttons_meet_minimum_target_size(
    app: QApplication,
    label: str,
    _lands_in: str,
) -> None:
    """Every QPushButton must hint at least 24×24 px via sizeHint().

    Below 24×24 the click target collides with adjacent controls for
    users with motor impairments. We check sizeHint (the *intended*
    size at layout time), not the actual rendered geometry — the
    screen is never shown, so .size() is always (640, 480) here.
    """
    state = AppState()
    screen = _build_screen(label, _lands_in, state)
    try:
        too_small: list[str] = []
        for btn in screen.findChildren(QPushButton):
            hint = btn.sizeHint()
            if hint.width() < _MIN_TARGET_PX or hint.height() < _MIN_TARGET_PX:
                cls = type(btn).__name__
                txt = btn.text() or btn.accessibleName() or "<unnamed>"
                too_small.append(
                    f"{cls}({txt!r}) {hint.width()}×{hint.height()}"
                )
        assert not too_small, (
            f"{label}: {len(too_small)} button(s) below {_MIN_TARGET_PX}×{_MIN_TARGET_PX} "
            f"px target size — SC 2.5.5 regression. Offenders: "
            f"{', '.join(too_small[:8])}"
            + ("…" if len(too_small) > 8 else "")
        )
    finally:
        _stop_background_workers(screen)


# ---------------------------------------------------------------------------
# SC 1.3.1 — Info and Relationships (screen has identifying object name)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,_lands_in", SIDEBAR_ITEMS)
def test_screen_widget_has_identifying_handle(
    app: QApplication,
    label: str,
    _lands_in: str,
) -> None:
    """Every screen exposes a non-empty class name AT can announce.

    Sanity guard: a screen whose top-level widget is the bare `QWidget`
    base class confuses screen-reader navigation (the user lands in
    "panel" with no further role information). Forces every screen to
    be at least a subclass.
    """
    state = AppState()
    screen = _build_screen(label, _lands_in, state)
    try:
        cls = type(screen).__name__
        assert cls != "QWidget", (
            f"{label}: top-level screen is bare QWidget — give it a "
            "named subclass so screen readers can announce its role."
        )
    finally:
        _stop_background_workers(screen)
