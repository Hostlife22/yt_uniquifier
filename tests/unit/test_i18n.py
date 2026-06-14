"""Tests for the v0.9.0 R5 GUI localization layer.

Covers the small, durable surface:
  * the translation table parses + every TRANSLATIONS locale only
    keys against SOURCE_KEYS (a typo here would mean a string is
    "translated" but never actually rendered)
  * coverage_ratio reports something sane for ru_RU
  * system_locale_hint honours LANG / LC_ALL / LC_MESSAGES
  * AppState round-trips locale through state.json
  * install_translator / removeTranslator interaction works on a
    real QApplication (skipped if QT_QPA_PLATFORM cannot offscreen)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from yt_uniquifier.gui.i18n import (
    SOURCE_LOCALE,
    available_locales,
    coverage_ratio,
    system_locale_hint,
)
from yt_uniquifier.gui.i18n.translations import SOURCE_KEYS, TRANSLATIONS

# ---------------------------------------------------------------------------
# Catalogue invariants
# ---------------------------------------------------------------------------


def test_source_keys_are_unique() -> None:
    assert len(set(SOURCE_KEYS)) == len(SOURCE_KEYS), (
        "duplicate entry in SOURCE_KEYS — coverage_ratio would over-count"
    )


def test_every_translation_key_exists_in_source() -> None:
    """A translation for a key not in SOURCE_KEYS is dead code."""
    src = set(SOURCE_KEYS)
    for locale, table in TRANSLATIONS.items():
        unknown = sorted(set(table) - src)
        assert not unknown, (
            f"{locale} has translations for unknown keys: {unknown[:5]}"
        )


def test_ru_RU_translations_are_non_empty_strings() -> None:
    ru = TRANSLATIONS["ru_RU"]
    for key, val in ru.items():
        assert isinstance(val, str) and val, (
            f"empty/non-string translation for {key!r}: {val!r}"
        )


def test_available_locales_includes_source_and_ru() -> None:
    locales = available_locales()
    assert SOURCE_LOCALE in locales
    assert "ru_RU" in locales


def test_coverage_ratio_source_is_one() -> None:
    assert coverage_ratio(SOURCE_LOCALE) == 1.0


def test_coverage_ratio_ru_is_substantial(caplog: pytest.LogCaptureFixture) -> None:
    """ru_RU should cover most CTAs; if a future commit drops a key,
    the test below logs the ratio so you see how far it slid."""
    ratio = coverage_ratio("ru_RU")
    # Surface the number so it appears in CI logs when the test runs.
    caplog.set_level("INFO")
    print(f"\nru_RU coverage: {ratio * 100:.1f}%")
    assert ratio >= 0.5, (
        f"ru_RU coverage dropped to {ratio:.2f}; add the missing entries "
        f"to TRANSLATIONS['ru_RU']."
    )


def test_coverage_ratio_unknown_locale_is_zero() -> None:
    assert coverage_ratio("xx_XX") == 0.0


# ---------------------------------------------------------------------------
# System locale hint
# ---------------------------------------------------------------------------


def test_system_locale_hint_reads_LANG(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    assert system_locale_hint() == "ru_RU"


def test_system_locale_hint_strips_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "fr_CA@euro")
    assert system_locale_hint() == "fr_CA"


def test_system_locale_hint_ignores_C_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "POSIX")
    assert system_locale_hint() is None


def test_system_locale_hint_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    assert system_locale_hint() is None


# ---------------------------------------------------------------------------
# AppState round-trip (locale persists to state.json)
# ---------------------------------------------------------------------------


def test_appstate_persists_and_reloads_locale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A locale set on one AppState reads back identically on the next.

    The QApplication-free import path requires that AppState's
    __init__ tolerate not having a Qt event loop; pytest-qt's
    qapp fixture is implicit if any test in the session opened a
    QApplication, otherwise we skip.
    """
    pytest.importorskip("PyQt6.QtCore")
    # Redirect STATE_PATH onto tmp_path so we never touch the user's
    # real state.json.
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(
        "yt_uniquifier.gui.state.STATE_PATH", state_path,
    )
    monkeypatch.setattr(
        "yt_uniquifier.gui.state.CONFIG_DIR", tmp_path,
    )

    # Force-load PyQt6.QtCore before AppState — pytest-qt usually
    # arranges this, but the offscreen platform plugin needs an env
    # hint that may not be set in this minimal context.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from yt_uniquifier.gui.state import AppState
    a = AppState()
    a.set_locale("ru_RU")
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["locale"] == "ru_RU"

    # Re-instantiate — should rehydrate ru_RU.
    b = AppState()
    assert b.locale == "ru_RU"


# ---------------------------------------------------------------------------
# install_translator on a real QApplication
# ---------------------------------------------------------------------------


def test_install_translator_translates_known_key() -> None:
    """End-to-end: install ru_RU and QCoreApplication.translate returns
    Russian for a known source string."""
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.i18n import (
        active_locale,
        install_translator,
    )

    app = QApplication.instance() or QApplication([])

    install_translator(app, "ru_RU")
    assert active_locale() == "ru_RU"
    # ``QCoreApplication.translate(context, source)`` consults the
    # installed translators; the RuntimeTranslator ignores context
    # (the dict is global) and matches on source.
    translated = QCoreApplication.translate("anywhere", "&Run")
    assert translated == "&Запустить", (
        f"expected Russian for &Run; got {translated!r}"
    )

    # Switch back to source — installer removes the ru_RU translator.
    install_translator(app, SOURCE_LOCALE)
    assert active_locale() == SOURCE_LOCALE
    # With no translator, Qt falls back to the source string verbatim.
    fallback = QCoreApplication.translate("anywhere", "&Run")
    assert fallback == "&Run"


def test_install_translator_unknown_locale_falls_back_silently() -> None:
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.i18n import active_locale, install_translator

    app = QApplication.instance() or QApplication([])
    install_translator(app, "xx_XX")
    # Unknown locale → silently treated as source; no exception.
    assert active_locale() == SOURCE_LOCALE


# ---------------------------------------------------------------------------
# v1.3.0 Task 35 — zh_CN, es, pt_BR coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale", ["zh_CN", "es", "pt_BR"])
def test_v1_3_locale_present_and_non_empty(locale: str) -> None:
    table = TRANSLATIONS[locale]
    for key, val in table.items():
        assert isinstance(val, str) and val.strip(), (
            f"{locale}: empty translation for {key!r}: {val!r}"
        )


@pytest.mark.parametrize("locale", ["zh_CN", "es", "pt_BR"])
def test_v1_3_locale_meets_30_key_floor(locale: str) -> None:
    """v1.3.0 roadmap demands ≥30 keys covered per new locale (parity
    with the en/ru baseline)."""
    table = TRANSLATIONS[locale]
    covered = sum(1 for k in SOURCE_KEYS if k in table)
    assert covered >= 30, f"{locale} covers {covered}/{len(SOURCE_KEYS)} keys; need ≥30"


def test_available_locales_includes_v1_3_set() -> None:
    locales = available_locales()
    assert "zh_CN" in locales
    assert "es" in locales
    assert "pt_BR" in locales
