"""Translation catalogues for the GUI (v0.9.0 R5).

* ``SOURCE_KEYS`` is the canonical set of strings the GUI wraps in
  ``self.tr(...)`` and is the only place we list them.
  ``coverage_ratio`` checks each locale against this set.
* ``TRANSLATIONS[<locale>][<source>]`` is what the runtime
  translator returns. Missing keys fall back to the source string,
  which is the documented graceful-degradation contract.

Contributing a new locale: copy the ``ru_RU`` block, replace the
right-hand values, add the locale code to ``TRANSLATIONS``. Run
``pytest tests/unit/test_i18n.py -v`` — the coverage test will
report your locale's coverage ratio in its log line.

Bilingual style for v0.9 (en → ru):

* CTAs use the Russian imperative (``Запустить``, ``Сохранить``).
* Status nouns stay nominative (``готово``, ``ошибка``).
* Mnemonics: the trailing ``&`` in source strings is preserved in
  the translation so Qt can mark the shortcut letter. The Russian
  side picks a Cyrillic letter close to the English mnemonic.
"""

from __future__ import annotations

# Source-of-truth list of every translatable string the v0.9 GUI
# wraps. Add an entry here when you wrap a new string with tr().
# Keep alphabetised within sections; section headers are comments.
SOURCE_KEYS: tuple[str, ...] = (
    # ---- Run screen ----
    "&Run",
    "&Cancel",
    "&Pause",
    "Auto-tune for this source",
    "Input file",
    "Output file",
    "Profile",
    "Encoder",
    "Workers",
    "Status: idle",
    "Status: running",
    "Status: completed",
    "Status: failed",
    "Status: cancelled",

    # ---- Settings screen ----
    "Settings",
    "Appearance",
    "Defaults",
    "Maintenance",
    "Language",
    "Theme",
    "Default profile",
    "Local telemetry (opt-in)",
    "Post-job notifications (webhook + SMTP)",
    "&Save",
    "&Reset encoder cache",
    "Open &log folder",
    "Open &config folder",

    # ---- Common dialogs ----
    "OK",
    "Cancel",
    "Apply",
    "Close",
    "Yes",
    "No",
)


# locale → {source: translation}. Missing keys fall back to source.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru_RU": {
        # ---- Run screen ----
        "&Run": "&Запустить",
        "&Cancel": "&Отмена",
        "&Pause": "&Пауза",
        "Auto-tune for this source": "Автонастройка для этого источника",
        "Input file": "Исходный файл",
        "Output file": "Выходной файл",
        "Profile": "Профиль",
        "Encoder": "Кодировщик",
        "Workers": "Потоки",
        "Status: idle": "Статус: ожидание",
        "Status: running": "Статус: выполняется",
        "Status: completed": "Статус: завершено",
        "Status: failed": "Статус: ошибка",
        "Status: cancelled": "Статус: отменено",

        # ---- Settings screen ----
        "Settings": "Настройки",
        "Appearance": "Внешний вид",
        "Defaults": "По умолчанию",
        "Maintenance": "Обслуживание",
        "Language": "Язык",
        "Theme": "Тема",
        "Default profile": "Профиль по умолчанию",
        "Local telemetry (opt-in)": "Локальная телеметрия (по согласию)",
        "Post-job notifications (webhook + SMTP)":
            "Уведомления о завершении (webhook + SMTP)",
        "&Save": "&Сохранить",
        "&Reset encoder cache": "&Сбросить кэш кодировщиков",
        "Open &log folder": "Открыть папку &логов",
        "Open &config folder": "Открыть папку &конфигурации",

        # ---- Common dialogs ----
        "OK": "ОК",
        "Cancel": "Отмена",
        "Apply": "Применить",
        "Close": "Закрыть",
        "Yes": "Да",
        "No": "Нет",
    },
}
