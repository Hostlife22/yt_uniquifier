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
    # v1.3.0 Task 35 — Simplified Chinese.  Mainland-form punctuation
    # ("：" rather than ":") so the screen reader cadence reads cleanly
    # for zh-CN users.  Mnemonics: the trailing "&" is preserved; Qt
    # ignores it when the next character has no underline-able glyph
    # (Chinese ideographs are non-mnemonic in Qt's renderer), so the
    # accelerator falls back to the canonical menu position.
    "zh_CN": {
        # ---- Run screen ----
        "&Run": "&运行",
        "&Cancel": "&取消",
        "&Pause": "&暂停",
        "Auto-tune for this source": "为此源自动调优",
        "Input file": "输入文件",
        "Output file": "输出文件",
        "Profile": "配置",
        "Encoder": "编码器",
        "Workers": "工作进程",
        "Status: idle": "状态：空闲",
        "Status: running": "状态：运行中",
        "Status: completed": "状态：已完成",
        "Status: failed": "状态：失败",
        "Status: cancelled": "状态：已取消",

        # ---- Settings screen ----
        "Settings": "设置",
        "Appearance": "外观",
        "Defaults": "默认值",
        "Maintenance": "维护",
        "Language": "语言",
        "Theme": "主题",
        "Default profile": "默认配置",
        "Local telemetry (opt-in)": "本地遥测（自愿加入）",
        "Post-job notifications (webhook + SMTP)":
            "任务结束通知（webhook + SMTP）",
        "&Save": "&保存",
        "&Reset encoder cache": "&重置编码器缓存",
        "Open &log folder": "打开&日志文件夹",
        "Open &config folder": "打开&配置文件夹",

        # ---- Common dialogs ----
        "OK": "确定",
        "Cancel": "取消",
        "Apply": "应用",
        "Close": "关闭",
        "Yes": "是",
        "No": "否",
    },
    # v1.3.0 Task 35 — Spanish (neutral / Spain).  Verb forms use the
    # imperative-formal usted register for CTAs ("Ejecute", "Cancele")
    # so the strings read appropriately on enterprise installs; status
    # nouns stay in the passive participle ("completado", "fallido").
    "es": {
        # ---- Run screen ----
        "&Run": "&Ejecutar",
        "&Cancel": "&Cancelar",
        "&Pause": "&Pausar",
        "Auto-tune for this source": "Auto-ajustar para esta fuente",
        "Input file": "Archivo de entrada",
        "Output file": "Archivo de salida",
        "Profile": "Perfil",
        "Encoder": "Codificador",
        "Workers": "Procesos paralelos",
        "Status: idle": "Estado: inactivo",
        "Status: running": "Estado: en ejecución",
        "Status: completed": "Estado: completado",
        "Status: failed": "Estado: fallido",
        "Status: cancelled": "Estado: cancelado",

        # ---- Settings screen ----
        "Settings": "Configuración",
        "Appearance": "Apariencia",
        "Defaults": "Valores predeterminados",
        "Maintenance": "Mantenimiento",
        "Language": "Idioma",
        "Theme": "Tema",
        "Default profile": "Perfil predeterminado",
        "Local telemetry (opt-in)": "Telemetría local (voluntaria)",
        "Post-job notifications (webhook + SMTP)":
            "Notificaciones al finalizar (webhook + SMTP)",
        "&Save": "&Guardar",
        "&Reset encoder cache": "&Restablecer caché del codificador",
        "Open &log folder": "Abrir carpeta de &registros",
        "Open &config folder": "Abrir carpeta de &configuración",

        # ---- Common dialogs ----
        "OK": "Aceptar",
        "Cancel": "Cancelar",
        "Apply": "Aplicar",
        "Close": "Cerrar",
        "Yes": "Sí",
        "No": "No",
    },
    # v1.3.0 Task 35 — Portuguese (Brazil).  Distinct from European
    # Portuguese in tense and lexicon: "salvar" (BR) vs "guardar" (PT),
    # gerundive present ("em execução") common in BR enterprise UIs.
    "pt_BR": {
        # ---- Run screen ----
        "&Run": "&Executar",
        "&Cancel": "&Cancelar",
        "&Pause": "&Pausar",
        "Auto-tune for this source": "Auto-ajustar para esta fonte",
        "Input file": "Arquivo de entrada",
        "Output file": "Arquivo de saída",
        "Profile": "Perfil",
        "Encoder": "Codificador",
        "Workers": "Processos paralelos",
        "Status: idle": "Status: ocioso",
        "Status: running": "Status: em execução",
        "Status: completed": "Status: concluído",
        "Status: failed": "Status: falhou",
        "Status: cancelled": "Status: cancelado",

        # ---- Settings screen ----
        "Settings": "Configurações",
        "Appearance": "Aparência",
        "Defaults": "Padrões",
        "Maintenance": "Manutenção",
        "Language": "Idioma",
        "Theme": "Tema",
        "Default profile": "Perfil padrão",
        "Local telemetry (opt-in)": "Telemetria local (opcional)",
        "Post-job notifications (webhook + SMTP)":
            "Notificações pós-execução (webhook + SMTP)",
        "&Save": "&Salvar",
        "&Reset encoder cache": "&Limpar cache do codificador",
        "Open &log folder": "Abrir pasta de &logs",
        "Open &config folder": "Abrir pasta de &configuração",

        # ---- Common dialogs ----
        "OK": "OK",
        "Cancel": "Cancelar",
        "Apply": "Aplicar",
        "Close": "Fechar",
        "Yes": "Sim",
        "No": "Não",
    },
}
