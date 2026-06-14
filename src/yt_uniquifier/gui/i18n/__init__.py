"""Localization for the desktop GUI (v0.9.0 R5).

Design choice for v0.9:

* The translation catalogue lives as a Python dict in
  :mod:`translations`. We deliberately skip the canonical
  ``.ts`` source / ``lrelease`` → ``.qm`` pipeline so the project
  has no Qt-side build dependency. Contributors edit Python; the
  wheel ships exactly what they wrote.

* A thin ``RuntimeTranslator(QTranslator)`` subclass overrides
  :py:meth:`QTranslator.translate` to return strings out of the
  dict. This is the same mechanism Qt uses internally for ``.qm``
  files — installing one on the QApplication makes every
  ``self.tr(...)`` / ``QObject.tr(...)`` call go through it.

* Source language is en-US. A locale that lacks a string falls
  back to the source verbatim, which is the desired QTranslator
  behaviour and means lower-traffic strings can stay English
  during incremental coverage work (see ``docs/i18n.md`` for the
  current matrix).

Future v1.0: migrate to ``.ts`` + ``pylupdate6`` + ``lrelease``
once translation volume warrants it. The public API of this module
will not change (``install_translator``, ``available_locales``).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt6.QtCore import QObject, QTranslator
    from PyQt6.QtWidgets import QApplication

_log = logging.getLogger(__name__)

# Source language; never needs translation entries.
SOURCE_LOCALE = "en_US"


def available_locales() -> list[str]:
    """Locales the wheel ships catalogues for (plus the source)."""
    from yt_uniquifier.gui.i18n.translations import TRANSLATIONS
    return [SOURCE_LOCALE, *sorted(TRANSLATIONS.keys())]


def system_locale_hint() -> str | None:
    """Best-effort guess of the user's preferred locale.

    Honoured at app boot when the persisted state.json has no
    explicit locale (i.e. first launch). Returns a normalised
    locale code (e.g. ``ru_RU``) or ``None`` if nothing parseable
    is in the env.
    """
    for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(env, "").strip()
        if not raw:
            continue
        # Strip ``.UTF-8`` or ``@variant`` suffixes; normalise dashes.
        base = raw.split(".")[0].split("@")[0].replace("-", "_")
        if base and base.lower() not in {"c", "posix"}:
            return base
    return None


def _make_translator() -> QTranslator:
    """Build a fresh RuntimeTranslator instance for the current locale.

    Wrapped in a function so callers that need to swap on a locale
    change (Settings combo) can replace the previous translator
    cleanly via ``app.removeTranslator`` / ``app.installTranslator``.
    """
    from PyQt6.QtCore import QTranslator

    from yt_uniquifier.gui.i18n.translations import TRANSLATIONS

    class RuntimeTranslator(QTranslator):
        def __init__(self, locale: str, parent: QObject | None = None) -> None:
            super().__init__(parent)
            self._locale = locale
            self._table = TRANSLATIONS.get(locale, {})

        # Qt 6 signature: (context, sourceText, disambiguation, n)
        def translate(
            self,
            context: bytes | str | None,
            sourceText: bytes | str | None,
            disambiguation: bytes | str | None = None,
            n: int = -1,
        ) -> str:
            if not sourceText:
                return ""
            key = (
                sourceText.decode("utf-8") if isinstance(sourceText, bytes)
                else sourceText
            )
            return self._table.get(key, "")

        def isEmpty(self) -> bool:
            return not self._table

    return RuntimeTranslator(_active_locale)


_active_locale: str = SOURCE_LOCALE
_installed: QTranslator | None = None


def install_translator(app: QApplication, locale: str) -> None:
    """Install a RuntimeTranslator for ``locale`` on ``app``.

    Removes any previously-installed translator from this module so
    a Settings → Language swap doesn't stack catalogues. If
    ``locale`` is unknown, falls back to the source locale silently;
    we never want a stray ``ru_FOO`` to crash the app.
    """
    global _active_locale, _installed
    if locale not in available_locales():
        _log.warning("unknown locale %r; falling back to %s",
                     locale, SOURCE_LOCALE)
        locale = SOURCE_LOCALE
    _active_locale = locale
    if _installed is not None:
        app.removeTranslator(_installed)
        _installed = None
    if locale == SOURCE_LOCALE:
        # Nothing to install — source strings are emitted verbatim.
        return
    translator = _make_translator()
    app.installTranslator(translator)
    _installed = translator


def active_locale() -> str:
    """Last locale handed to ``install_translator`` (for tests + Settings)."""
    return _active_locale


def coverage_ratio(locale: str) -> float:
    """Fraction of cataloged keys translated for ``locale`` (0..1).

    Useful for the doc snippet and a smoke test that flags a fresh
    locale shipped with zero strings.
    """
    from yt_uniquifier.gui.i18n.translations import (
        SOURCE_KEYS,
        TRANSLATIONS,
    )
    if locale == SOURCE_LOCALE:
        return 1.0
    table = TRANSLATIONS.get(locale, {})
    if not SOURCE_KEYS:
        return 0.0
    hits = sum(1 for k in SOURCE_KEYS if table.get(k))
    return hits / len(SOURCE_KEYS)


__all__ = [
    "SOURCE_LOCALE",
    "active_locale",
    "available_locales",
    "coverage_ratio",
    "install_translator",
    "system_locale_hint",
]
