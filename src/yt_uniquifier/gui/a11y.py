"""Accessibility helpers for the GUI layer (v0.7 R2 / E1 sweep).

Centralizes the boilerplate of setting `accessibleName`,
`accessibleDescription`, and `shortcut` on Qt widgets so every screen
follows the same conventions. The accompanying regression test in
`tests/unit/test_gui_accessibility.py` walks the screen tree and
asserts that every interactive widget has a non-empty
`accessibleName()` — running `mark()` is the simplest way to satisfy
that contract.

Conventions:
    * `name` must be non-empty and stable across renders (assistive
      tech keys actions to it).
    * `description` is the long-form context: defaults to the widget's
      tooltip when the tooltip is set, otherwise None.
    * Buttons that show a glyph (e.g. ``"▶ Run"``) should set a clean
      `name="Run"` so the screen reader announces "Run" instead of
      "play triangle run".
"""

from __future__ import annotations

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QPushButton, QWidget

# Public re-export so callers don't need a second import.
__all__ = [
    "INTERACTIVE_WIDGET_CLASSES",
    "mark",
    "shortcut_for",
]


def mark(
    widget: QWidget,
    name: str,
    description: str | None = None,
    *,
    shortcut: str | None = None,
) -> None:
    """Attach accessibility metadata to a widget.

    `name` is required and must be non-empty. `description` is set
    only if provided (avoids overwriting a tooltip-derived default).
    `shortcut` accepts the same syntax as ``QKeySequence`` (e.g.
    ``"Ctrl+R"``); applied only when the widget is a ``QPushButton``
    or supports ``setShortcut`` directly.
    """
    if not name:
        raise ValueError("a11y.mark() requires a non-empty name")
    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    if shortcut:
        # Some widgets (QLabel, QFrame) don't have setShortcut at all.
        # `getattr(..., None)` lets us add metadata uniformly without
        # exploding on non-shortcuttable widgets.
        setter = getattr(widget, "setShortcut", None)
        if callable(setter):
            setter(QKeySequence(shortcut))


def shortcut_for(button: QPushButton, sequence: str) -> None:
    """Convenience: attach a keyboard shortcut to a button.

    Equivalent to ``button.setShortcut(QKeySequence(sequence))`` but
    raises a clear error if the caller forgets to mark the button with
    `mark()` first (the screen-reader pass would otherwise silently
    miss the unnamed primary CTA).
    """
    if not button.accessibleName():
        raise ValueError(
            f"shortcut_for: button at sequence={sequence!r} has no "
            "accessibleName; call a11y.mark() first.",
        )
    button.setShortcut(QKeySequence(sequence))


# The set of QWidget subclasses considered "interactive" for the
# regression-test walker. Edits and additions should stay narrow:
# the walker fails CI when one of these is missing accessibleName, so
# expanding the set is a deliberate, breaking change.
INTERACTIVE_WIDGET_CLASSES: tuple[str, ...] = (
    "QPushButton",
    "QComboBox",
    "QSpinBox",
    "QDoubleSpinBox",
    "QLineEdit",
    "QCheckBox",
    "QRadioButton",
    "QSlider",
    "QListWidget",
    "QTreeWidget",
)
