<!-- cspell:disable -->
# Plan: yt-uniquifier v0.7.0 — GUI maturity + Platform profiles

**Source PRD**: `.claude/plans/yt-uniquifier-best-in-class.plan.md` § «v0.7.0»
**Selected Milestone**: v0.7.0 (3-4 недели, 12 чекбоксов)
**Complexity**: **Large** — multi-file sweep, новые RunEvent контракты, 5 новых YAML, accessibility audit, новый notifications-модуль, signal-handling для pause/resume
**Branch strategy**: feature-branches `v0.7-r1` .. `v0.7-r5`, по одной round-сущности на коммит (как в v0.5.5/v0.6.0)

---

## Summary

v0.7.0 закрывает разрыв «архитектурно лидер, UX отстаёт». Шесть фич-эпиков (F2, F3, F4, F5, F7) дают пользователю то, что есть у HandBrake/Shutter Encoder/360°Uniquizer: живую обратную связь во время encode, готовые пресеты под платформы (TikTok/YT/Reels/Shorts/LinkedIn), пост-job нотификации, паузу, и one-click авто-калибровку. Параллельно семь полировочных задач (E1, E3, E4, E6, E12, C1–C7, WCAG-AA regression) делают GUI accessible, theme-safe и PyInstaller-portable.

**Не входит**: F6 (SSCD), F10 (SQLite corpus), F11 (per-segment VMAF target). Это v0.8.0.

---

## Patterns to Mirror

| Категория | Источник | Паттерн |
|---|---|---|
| Worker → Qt signal bridge | `src/yt_uniquifier/gui/workers/run_worker.py:1-50` + `workers/base.py:23-41` | `WorkerBase(QThread)` экспонирует 5 базовых сигналов; subclass добавляет typed `pyqtSignal(...)` поверх. Новый `divergence` сигнал в RunWorker встраивается тем же способом. |
| RunEvent контракт | `core/models.py::RunEvent` (frozen dataclass, `kind: str`, `payload: dict`) | Добавление нового kind не ломает существующих consumers — они фильтруют по `kind`. Никакого версионирования контракта не нужно. **A1 invariant (immutability of payload)** соблюдать. |
| Профильная YAML-схема | `src/yt_uniquifier/profiles/medium.yaml` + `core/profile_loader.py::load_profile` | `extra=forbid` валидация. Новые profiles только используют существующие `transforms[].id`. Если нужен новый transform (aspect-fit) — отдельный submodule + register в `core/transforms/__init__.py`. |
| Config-path централизация | `gui/state.py:35` (единственное место `Path.home() / ".config"`) | Менять одну константу, остальные screens наследуют. Migration: lazy-copy при первом запуске, если новый путь пуст, а старый существует. |
| Cancel + cancel_token plumbing (v0.5.5 hotfix #4) | `core/calibration/loop.py::calibrate(..., cancel_token=None)` | Любой новый long-running путь (Pause/Resume) принимает `cancel_token`. Идём по тому же контракту. |
| Theme-driven styling | `gui/theme.py::THEMES` + `gui/widgets/log_console.py` (правильно отрисован) | Цвета read'аются из `state.theme()` или нового `theme.token("badge_fail")`. preflight_panel + kpi_pills сейчас хардкодят — переписать на `theme.tokens` lookup. |
| Worker-уровень accessibility (HandBrake-style) | n/a (новое) | На каждый `QPushButton`, `QComboBox`, `QSpinBox` — `setAccessibleName` + `setAccessibleDescription`, `setShortcut` где есть primary CTA, `setTabOrder(a, b)` в `_init_ui`. |

---

## Architecture decisions (locked before code)

### F2 — Live divergence indicator

**Где считать**: внутри `core/segmenter.py::process_video_segment` после успешного re-encode сегмента — на encoded `seg_NNNN.mkv` против slice исходника `[start..end]`. Аудио — раз в N сегментов (или на финальной concat-фазе, по готовому output).

**Что эмитим** — новый `RunEvent.kind = "divergence_sample"` с payload:
```python
{
    "segment": 12,
    "phash_similarity": 0.987,   # 1 - mean_hamming/64
    "audio_jaccard": None,       # заполняется на audio pass
    "running_phash": 0.984,      # exp-weighted moving average
}
```

**Render** — новый widget `gui/widgets/divergence_indicator.py`: горизонтальная полоска + текущий score + sparkline последних 30 сегментов (используем уже существующий `chart_widget.py` как базу).

**Cost-budget**: pHash на 4 кадра/сегмент ≈ 50ms (через уже-имеющийся `phash.sample_frames` из `qa/phash.py`). Опция в Settings: «Live divergence sampling: off/light(every 4th seg)/full». Default — `light` для full-length runs (> 10 min).

### F3 — Platform profiles

Текущая профильная схема **не имеет** target-aspect / target-resolution транформов — только color/noise/audio. Чтобы шипнуть TikTok 9:16, Reels 9:16, Shorts 9:16, LinkedIn 1:1, YouTube 16:9-4K — нужен новый transform `video.fit_aspect` (params: `target_aspect: str` («9:16»/«1:1»/«16:9»), `mode: Literal["crop", "pad_blur", "pad_black"]`, `target_width: int | None`, `target_height: int | None`).

**Решение**: добавить **минимальный** `video.fit_aspect` builder, отрабатывающий типовые случаи crop-center и pad-with-blurred-bg (через `split[a][b]; [a]scale=W:H:force_original_aspect_ratio=increase,crop=W:H; [b]scale=W:H:force_original_aspect_ratio=decrease,gblur=10`). pad_black — простой `pad=W:H:(ow-iw)/2:(oh-ih)/2:color=black`.

5 profile-файлов:
- `youtube_4k.yaml` — 3840×2160 16:9, h264 high@5.2, loudnorm −14 LUFS, soft transforms
- `youtube_1080p.yaml` — bonus добавим, 1920×1080 (классика)
- `tiktok_vertical.yaml` — 1080×1920 9:16, crop-center, loudnorm −16 LUFS
- `instagram_reels.yaml` — 1080×1920 9:16, pad_blur (Instagram-style), loudnorm −16 LUFS
- `instagram_square.yaml` — 1080×1080 1:1, pad_blur, loudnorm −16 LUFS
- `youtube_shorts.yaml` — 1080×1920 9:16, crop-center, loudnorm −14 LUFS
- `linkedin_square.yaml` — 1080×1080 1:1, crop-center, loudnorm −16 LUFS

Минимум по чек-листу — 5 (TikTok, Reels, Shorts, Square, YouTube). Шипнем 7 — дополнительные стоят 5 строк YAML каждый.

### F4 — Post-job notifications

Новый модуль `core/notifications.py`:
- `class NotificationConfig(BaseModel)`: `webhook_url: HttpUrl | None`, `smtp: SmtpConfig | None`, `events: list[Literal["completed", "failed"]]`
- `class SmtpConfig`: host, port, username, **password из keyring/env**, sender, recipients
- `def notify(event: RunEvent, config: NotificationConfig) -> None` — best-effort, never raises (логирует WARNING). Discord/Slack/Telegram = `webhook_url` с правильным форматом payload (auto-detect по host: `discord.com/api/webhooks` → Discord embed, `hooks.slack.com` → Slack blocks, `api.telegram.org` → sendMessage).
- Webhook timeout = 5s, SMTP timeout = 10s.

**Хук в orchestrator**: после `RunSummary` в `core/orchestrator.py::run_full` — вызвать `notify(...)` если конфиг определён. Конфиг читается из GUI state (`state.notifications`) или CLI flag `--notify-config path.yaml`.

**Settings UI**: новая `QGroupBox("Post-job notifications")` в `gui/screens/settings.py` — webhook URL field + «Test» button + SMTP collapsible block + event checkboxes.

**Безопасность**: SMTP пароль НЕ хранится plaintext в `state.json`. Использовать `keyring` package (lazy import — добавить в `[gui]` extras). Если keyring недоступен — env var `YT_UNIQUIFIER_SMTP_PASSWORD`. Если ни keyring, ни env — SMTP disabled с явным WARNING в UI.

### F5 — Pause / Resume in-progress

**POSIX**: SIGTSTP → ffmpeg subprocess получает SIGSTOP (через `proc.send_signal(signal.SIGSTOP)`), потом SIGCONT через `proc.send_signal(signal.SIGCONT)`.

**Windows**: `psutil.Process(proc.pid).suspend()` / `.resume()` (используем уже-задействованный psutil в `core/encoder.py`).

**State marker**: добавить в `state.json` поле `"paused_at": iso_timestamp | None`, чтобы на crash во время pause при resume runner понимал, что нужно сначала возобновить subprocess.

**GUI**: новая кнопка `Pause/Resume` в Run screen (рядом с Cancel). Сигнал → `runner.pause()` / `runner.resume()`. Состояние reflected в KPI («▶ encoding» / «⏸ paused at segment 42»).

**Контракт**: `core/runner.py::Runner.pause()` и `.resume()` no-op, если нет активного subprocess. Pause без resume в течение 24h → автоматический cancel (предохранитель против забытых пауз).

### F7 — Auto-calibrate в Run screen

Единая кнопка «🎯 Auto-tune for this source» рядом с «Run»:
1. Создаёт временную копию текущего profile_path
2. Запускает `CalibrateWorker` с этой копией + источником из Run-экрана
3. По завершении — сохраняет результат как `<profile_name>.tuned.yaml` рядом с оригиналом
4. Switch'ит state.profile_path на tuned версию
5. Показывает diff в QMessageBox («pitch 1.0008 → 1.0012, noise.strength 4 → 5; saved as soft.tuned.yaml. Run now?»)

Workhorse — `core/calibration/loop.py::calibrate` уже всё умеет (после v0.5.5 cancel_token plumbing). Только GUI-обёртка.

### E1 — Accessibility

Систематический sweep по 10 экранам + 7 widgets:
- `setAccessibleName(...)` на каждый interactive widget (button, combo, spin, list, picker)
- `setAccessibleDescription(...)` на elements которые показывают данные (KPI pills, preflight badges)
- `setTabOrder(self.field_a, self.field_b)` в `_init_ui` после `setLayout` (Qt не знает логику)
- `setShortcut(QKeySequence("Ctrl+R"))` на primary CTAs:
  - Run → Ctrl+R
  - Cancel → Esc
  - Open file → Ctrl+O
  - Settings → Ctrl+,
  - Switch screens → Ctrl+1..0
- Мнемоники в button text: `"&Run"`, `"&Cancel"`, `"&Browse..."`

Regression: pytest-qt test, который для каждой `QWidget` проверяет `accessibleName() != ""` для интерактивных widgets.

### E3 — QStandardPaths.AppConfigLocation

Заменить `gui/state.py:35`:
```python
from PyQt6.QtCore import QStandardPaths
CONFIG_DIR = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "yt_uniquifier"
```

Это даёт правильные пути на всех OS:
- macOS: `~/Library/Application Support/yt_uniquifier`
- Windows: `%APPDATA%\yt_uniquifier`
- Linux (с XDG_CONFIG_HOME): `$XDG_CONFIG_HOME/yt_uniquifier`
- Linux (без): `~/.config/yt_uniquifier`

**Migration helper**: при первом старте, если `CONFIG_DIR` пуст, но `~/.config/yt_uniquifier` существует — `shutil.copytree(...)` (не move — оставляем старый для отката).

### E4 — Theme leaks

`widgets/preflight_panel.py:17-19` — `_BADGE_STYLES` хардкодит цвета. Переписать как функцию `_badge_style(theme: str, level: str)` которая берёт из `gui/theme.py`:
```python
THEMES["dark"]["badge_fail"] = "background: #a83b3b; color: white;"
```

`widgets/kpi_pills.py:107` — `pill.setStyleSheet(f"background: {color}; color: white; ...")` — `color` берётся из `_color_for_level(...)`, которая тоже хардкодит. Маршрутизировать через `theme.tokens(state.theme())`.

После: подписаться на `state.theme_changed` сигнал и пересоздать stylesheet.

### E6 — Global sys.excepthook

В `gui/app_pyqt.py::main` после `QApplication` создания:
```python
def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    dlg = QMessageBox(QMessageBox.Icon.Critical, "yt-uniquifier crashed", str(exc))
    dlg.setDetailedText(text)
    dlg.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
    # ...
    sys.__excepthook__(exc_type, exc, tb)  # also stderr

sys.excepthook = _excepthook
```

Bonus: написать excepthook также в файл `<CONFIG_DIR>/crash.log` (append) — для bug-reports.

### E12 — importlib.resources для PROFILES_DIR

7 файлов сейчас содержат `PROFILES_DIR = Path(__file__).parents[2] / "profiles"`. Это **ломается под PyInstaller** (когда `__file__` → `_MEIPASS/...`, depending on bundle layout).

Заменить на:
```python
# в новом src/yt_uniquifier/gui/paths.py
from importlib.resources import files

def profiles_dir() -> Path:
    return Path(files("yt_uniquifier") / "profiles")
```

И во всех screens: `from yt_uniquifier.gui.paths import profiles_dir; PROFILES_DIR = profiles_dir()`.

Аналогично для `validation.py:47,61` `editable_root` / `REPO_ROOT` — но это сложнее, потому что dev-mode vs PyInstaller-mode разные. Решение: `tools/` копировать в `data_files` через PyInstaller spec ИЛИ переписать `validation.py` чтобы НЕ полагался на `tools/`-script, а импортировал функции из `core/` directly (это совпадает с E11 из master-плана, layering violation).

### C1–C7 — типизация

Перечисленные 7 `type: ignore`:
- `core/probe.py:257,262,267,272` — заменить на `frozenset(get_args(LiteralType))` membership check (mypy выводит точный тип сам).
- `cli/cmd_worker.py:138`, `cli/cmd_batch.py:107` — изменить сигнатуру `_process_one(profile: Profile)` чтобы исключить тип `Profile | None`.
- `gui/screens/history.py:101` — добавить `-> QWidget` return type.

### WCAG-AA regression test

Новый `tests/gui/test_contrast.py`: для каждой темы (light/dark) пройтись по `gui/theme.py::THEMES` цветам и проверить min contrast ratio ≥ 4.5 (для normal text) / 3.0 (для large text). Считать через стандартную W3C формулу `(L1 + 0.05) / (L2 + 0.05)`.

Также: walker по каждому экрану под `QT_QPA_PLATFORM=offscreen`, для каждой `QLabel` / `QPushButton` достаёт пары (foreground, background) из stylesheet и проверяет контраст. Это harder — может быть отложено на v0.7.1.

---

## Files to Change

| Файл | Действие | Причина |
|---|---|---|
| `src/yt_uniquifier/core/models.py` | EDIT | F2: документировать новый `RunEvent.kind = "divergence_sample"` |
| `src/yt_uniquifier/core/segmenter.py:200-300` | EDIT | F2: emit `divergence_sample` после re-encode сегмента; флаг `sample_phash` (env / RunOptions) |
| `src/yt_uniquifier/core/orchestrator.py` | EDIT | F2: пробросить `sample_phash` опцию; F4: вызвать `notifications.notify(...)` в финале |
| `src/yt_uniquifier/core/notifications.py` | CREATE | F4: модуль webhook + SMTP, auto-detect (Discord/Slack/Telegram) |
| `src/yt_uniquifier/core/transforms/video_fit_aspect.py` | CREATE | F3: новый transform для platform aspect ratios |
| `src/yt_uniquifier/core/transforms/__init__.py` | EDIT | F3: import video_fit_aspect |
| `src/yt_uniquifier/profiles/youtube_4k.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/youtube_1080p.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/youtube_shorts.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/tiktok_vertical.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/instagram_reels.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/instagram_square.yaml` | CREATE | F3 |
| `src/yt_uniquifier/profiles/linkedin_square.yaml` | CREATE | F3 |
| `src/yt_uniquifier/core/runner.py:200-280` | EDIT | F5: `Runner.pause()` / `resume()` через `proc.send_signal(SIGSTOP)` / psutil.suspend |
| `src/yt_uniquifier/core/checkpoint.py` | EDIT | F5: persist `paused_at` поле |
| `src/yt_uniquifier/gui/paths.py` | CREATE | E12: централизованный `profiles_dir()` через `importlib.resources` |
| `src/yt_uniquifier/gui/state.py:35` + migration | EDIT | E3: `QStandardPaths.AppConfigLocation`; migration helper |
| `src/yt_uniquifier/gui/screens/{settings,batch,calibrate,profile_editor,run,queue,validation}.py` | EDIT | E12: `from yt_uniquifier.gui.paths import profiles_dir` |
| `src/yt_uniquifier/gui/screens/run.py` | EDIT | F2: подключить divergence widget; F7: Auto-tune button; E1: accessibility; F5: Pause button |
| `src/yt_uniquifier/gui/screens/batch.py` | EDIT | F2: divergence per file; E1 |
| `src/yt_uniquifier/gui/screens/settings.py` | EDIT | F4: notifications GroupBox + test button; E1 |
| `src/yt_uniquifier/gui/screens/{calibrate,corpus,history,profile_editor,qa_viewer,queue,validation}.py` | EDIT | E1: accessibility sweep |
| `src/yt_uniquifier/gui/widgets/divergence_indicator.py` | CREATE | F2: sparkline + current score widget |
| `src/yt_uniquifier/gui/widgets/preflight_panel.py:17-75` | EDIT | E4: theme-driven styles, subscribe `theme_changed` |
| `src/yt_uniquifier/gui/widgets/kpi_pills.py:107` | EDIT | E4: theme-driven styles |
| `src/yt_uniquifier/gui/theme.py` | EDIT | E4: add `badge_fail/warn/ok` + `kpi_*` tokens |
| `src/yt_uniquifier/gui/workers/run_worker.py` | EDIT | F2: forward `divergence_sample` RunEvent → `pyqtSignal(dict)`; F5: pause/resume signals |
| `src/yt_uniquifier/gui/workers/notifications_test_worker.py` | CREATE | F4: «Test» button non-blocking probe |
| `src/yt_uniquifier/gui/app_pyqt.py` | EDIT | E6: global sys.excepthook + crash.log |
| `src/yt_uniquifier/core/probe.py:257-272` | EDIT | C1: replace `# type: ignore[return-value]` |
| `src/yt_uniquifier/cli/cmd_worker.py:138` + `cmd_batch.py:107` | EDIT | C2: tighten `Profile` type |
| `src/yt_uniquifier/gui/screens/history.py:101` | EDIT | C7: add return annotation |
| `pyproject.toml` | EDIT | F4: add `keyring` to `[gui]` extras (optional via try/except) |
| `tests/unit/test_notifications.py` | CREATE | F4 regression |
| `tests/unit/test_video_fit_aspect.py` | CREATE | F3 snapshot test |
| `tests/integration/test_platform_profiles.py` | CREATE | F3: each profile encodes a 3-sec source without error |
| `tests/unit/test_pause_resume.py` | CREATE | F5 regression (mock subprocess) |
| `tests/integration/test_pause_resume_real_ffmpeg.py` | CREATE | F5: real ffmpeg, signal-driven |
| `tests/gui/test_accessibility.py` | CREATE | E1: walks screens, asserts accessibleName |
| `tests/gui/test_theme_contrast.py` | CREATE | WCAG-AA regression |
| `tests/gui/test_excepthook.py` | CREATE | E6: dialog rendered on uncaught |
| `tests/gui/test_config_path_migration.py` | CREATE | E3: legacy → new path |
| `tests/unit/test_divergence_event.py` | CREATE | F2: RunEvent kind + payload contract |

Roughly **44 файла**: 27 EDIT, 17 CREATE. Tests составляют ~30% объёма.

---

## Tasks

Разбиваем v0.7.0 на **6 rounds**, каждая = один атомарный коммит с зелёными тестами. Порядок: безопасные сначала, рискованные (F5 signal handling) последними.

### Round 1 — Plumbing & polish (низкий риск, разблокирует остальное) — ✅ DONE

- [x] **C1–C7** type-ignore elimination — все 7 `# type: ignore` удалены. `core/probe.py` 4× через `cast(...)`, `cli/cmd_worker.py` + `cmd_batch.py` через `profile: Profile` (не `object`), `gui/screens/history.py` через `-> QWidget` annotation.
- [x] **E3** `QStandardPaths.AppConfigLocation` + migration helper. `gui/state.py::_resolve_config_dir()` + `_migrate_from_legacy()` (best-effort `copytree` со старого `~/.config/yt_uniquifier/`, legacy preserved). +4 unit теста (`tests/unit/test_gui_config_path_migration.py`). На macOS теперь использует `~/Library/Preferences/yt_uniquifier/`.
- [x] **E12** `gui/paths.py::profiles_dir()` через `importlib.resources.files()`. 7 screens переведены с `Path(__file__).parents[2] / "profiles"`.
- [x] **E4** theme tokens — `badge_{fail,warn,ok}_{bg,fg}` + `kpi_{red,yellow,green,neutral,fg}` добавлены в DARK_TOKENS и LIGHT_TOKENS. Новый `tokens_for(theme: str) -> dict`. `preflight_panel.py` и `kpi_pills.py` переписаны: принимают `state` через ctor, подписываются на `theme_changed`, перерисовываются при смене темы. +4 regression теста (`tests/unit/test_gui_theme_tokens.py`).

Validate: ✅ `ruff check .` — All checks passed. ✅ `mypy src/` — 109 files, no issues. ✅ `grep -rn "type: ignore" src/` — empty. ✅ 23/23 GUI tests pass. ✅ 643/643 (1 skip) unit suite — only known pre-existing flake `test_force_bypasses_cache` от v0.6.0 B6 (concurrent ThreadPoolExecutor + MagicMock race), не из R1.

**Files (16 modified, 4 created)**: `cli/cmd_{batch,worker}.py`, `core/probe.py`, 7 `gui/screens/*.py`, `gui/state.py`, `gui/theme.py`, `gui/widgets/{preflight_panel,kpi_pills}.py`, `tests/unit/test_gui_widgets.py`. New: `gui/paths.py`, `tests/unit/test_gui_config_path_migration.py`, `tests/unit/test_gui_theme_tokens.py`, this plan file.

**Carry-over**: pre-existing flake `tests/unit/test_encoder_detect.py::test_force_bypasses_cache` (call_count race under parallel suite run; passes in isolation). Track for v0.7.0 R2 (E1/E6 round) or earlier fix-once if blocking CI.

### Round 2 — Accessibility sweep (E1) + sys.excepthook (E6) — 🟡 R2.1 SHIPPED

R2 разбит на R2.1 (framework + Run + Settings + E6) и R2.2/R2.3 (остальные 8 screens). Это сохраняет blast-radius каждого коммита управляемым.

**R2.1 (этот commit):**
- [x] **a11y framework** — новый `gui/a11y.py` с `mark(widget, name, description, shortcut)` хелпером + `INTERACTIVE_WIDGET_CLASSES` allow-list для walker'а.
- [x] **MainWindow** — Ctrl+1..Ctrl+0 shortcuts на 10 sidebar entries; sidebar получает accessibleName + description.
- [x] **Run screen** — `mark()` на profile_combo, edit_profile_btn, preflight_btn (Ctrl+P), run_btn (Ctrl+R), cancel_btn (Esc), open_qa_btn (Ctrl+Q). Мнемоники: &Preflight/&Run/&Cancel/&QA.
- [x] **Settings screen** — `mark()` на theme_combo, default_profile_combo, recents_cap_spin, history_cap_spin, reset_enc_btn, open_logs_btn, open_config_btn, save_btn (Ctrl+S).
- [x] **Widgets** — file_picker (browse_btn, accessible name derived from `kind`), encoder_selector (accessibleName + description), log_console (filter_combo, copy_btn, clear_btn, text-area).
- [x] **E6 sys.excepthook** — `_install_global_excepthook()` в `app_pyqt.py::main`. Append-only `CONFIG_DIR/crash.log` с 100 KiB ротацией → `.log.1`. QMessageBox modal с DetailedText (Copy works). KeyboardInterrupt не показывает диалог. Hook никогда не raise'ит сам.
- [x] **Tests** — `tests/unit/test_gui_accessibility.py` (12 testcases: walker по 10 screens с pending-list; Run + Settings unskipped, остальные 8 deferred). `tests/unit/test_gui_excepthook.py` (6 testcases: write/append/rotate/KeyboardInterrupt/swallow-internal-errors/path-helper).

Validate: ✅ `ruff check .` All checks passed. ✅ `mypy src/` 110 files no issues. ✅ 678 unit + 4 GUI pass, 10 skipped (8 R2 pending, 1 PyQt6.QtCharts, 1 GUI imports). Только known pre-existing flake `test_force_bypasses_cache` (v0.6.0 B6).

**R2.2 (this commit) — DONE**: a11y sweep on remaining 8 screens. `_R2_PENDING` is empty.

- [x] **Corpus** — add/remove/refresh buttons marked; мнемоники &Add/&Remove/Re&fresh.
- [x] **QA Viewer** — browse, compute (Ctrl+R), cancel (Esc), open-in-browser marked.
- [x] **Profile Editor** — profile_combo, save (Ctrl+S), save-as (Ctrl+Shift+S), reload-list, seed_combo marked.
- [x] **Batch** — both browse buttons, pattern_edit, profile_combo, continue_check, run (Ctrl+R), cancel (Esc) marked.
- [x] **Calibrate** — profile_combo, 2 DoubleSpinBox (target / quality), 2 SpinBox (iterations / clip), run (Ctrl+R), cancel (Esc), save (Ctrl+S) marked.
- [x] **Queue** — root browse, init, add-files, reset-stale, profile_combo, workers spin, stop-when-empty check, output browse, start (Ctrl+R), stop (Esc) marked.
- [x] **Validation** — back/next nav, profile, variant count spin, output browse, generate (Ctrl+R), save-csv (Ctrl+S), correlation analysis (Ctrl+R) marked.
- [x] **History** — filter line-edit, clear-all button, per-row "Open output" + "QA" buttons marked with source-name context for screen reader.

**Test hardening**:
- New `_stop_background_workers(screen)` helper in test_gui_accessibility.py joins every QThread on the screen before parametrized teardown. Without it, CorpusListWorker and QueueStatusWorker (which auto-start in `__init__`) leaked C++ threads past test boundaries → Qt aborts with "QThread destroyed while running".
- `qa_viewer.py` now skips the `QtWebEngineWidgets` import entirely under `QT_QPA_PLATFORM=offscreen`. PyQt6 6.11.x QtWebEngineCore state went inconsistent after multiple QApplication recreations, aborting the test suite. TYPE_CHECKING + assert pattern keeps mypy happy without runtime cost.

Validate: ✅ ruff All checks passed. ✅ mypy 110 files no issues. ✅ 686 unit + smoke pass, 2 skip, 1 pre-existing flake (test_force_bypasses_cache, v0.6.0 B6). ✅ All 12 a11y tests green across 10 screens.

**Files (R2 cumulative)**: R2.1 + R2.2 = 9 screens, 3 widgets, app shell, 3 test files. R2.2 adds: 8 screens × ~6 marks each ≈ 50 mark() calls, 1 test-fixture helper, qa_viewer offscreen guard.

**R2 is closed.** Round 3 (F3 platform profiles + new `video.fit_aspect` transform) is next.

### Round 3 — Platform profiles (F3) — ✅ DONE

- [x] **`video.fit_aspect`** транформ: 3 modes (crop / pad_blur / pad_black) × 5 aspects (16:9, 9:16, 1:1, 4:5, 4:3) с per-aspect default dims + опциональным override target_width/target_height. pad_color injection-guard (тот же `_SAFE_COLOR_RE` что и `video.rotate`). pad_blur использует internal split+overlay через `__IN__` placeholder pattern (как у `video.blend_b`).
- [x] **20 snapshot тестов** в `tests/unit/test_video_fit_aspect.py`: все 3 modes × несколько aspects + per-aspect default dims, override логика, injection rejection, схема валидация (extra=forbid, bounded blur_sigma, supported aspect/mode literals).
- [x] **7 YAML профилей**: youtube_4k (3840×2160), youtube_1080p (1920×1080), youtube_shorts (1080×1920), tiktok_vertical (1080×1920 -16 LUFS), instagram_reels (pad_blur 9:16), instagram_square (pad_blur 1:1), linkedin_square (1080×1080).
- [x] **8 integration тестов** в `tests/integration/test_platform_profiles.py`: каждый профиль encode'ит `tiny_clip` через реальный ffmpeg → output landed at expected (W, H) within ±2 px even-dim guard. Плюс защитный test_every_shipped_profile_has_integration_coverage — fail если новый platform YAML забыли добавить в parametrize table.
- [x] **Регрессия в test_named_invariants.py** — добавил `video.fit_aspect` в `_TRANSFORMS_WITHOUT_DEFAULTS` (target_aspect required, нет sensible default).
- [ ] Docs `docs/profiles.md` — раздел "Platform profiles" не дописан (откладываю на R3.1 / doc-updater).

Validate: ✅ ruff All checks passed. ✅ mypy 111 files no issues. ✅ 707 unit + 2 skip + 1 deselect. ✅ 8/8 integration tests pass (~22s включая 7 real ffmpeg encodes). ✅ Все 20 snapshot tests зелёные. Только known pre-existing flake `test_force_bypasses_cache` остаётся.

**Files (R3)**: 2 created (`core/transforms/video_fit_aspect.py`, `tests/unit/test_video_fit_aspect.py`, `tests/integration/test_platform_profiles.py`), 2 modified (`core/transforms/__init__.py`, `tests/unit/test_named_invariants.py`), 7 created YAMLs. Total: 12 new files, 2 modified.

### Round 4 — F2 Live divergence + F7 Auto-calibrate

- F2: RunEvent kind `divergence_sample` + segmenter emit + new widget + Run/Batch wiring + sampling настройка в Settings
- F7: Auto-tune button в Run screen → CalibrateWorker → save tuned.yaml → switch state.profile_path
- Tests: `test_divergence_event.py` (контракт), GUI smoke что widget рендерится

Validate: smoke run на tiny_clip с `--sample-phash light` логирует RunEvent с phash_similarity в [0..1], GUI рисует sparkline.

### Round 5 — F4 Notifications

- `core/notifications.py` модуль (Discord/Slack/Telegram auto-detect + SMTP)
- Hook в orchestrator
- Settings GroupBox + Test button worker
- `keyring` optional dependency + fallback на env var
- Tests: `test_notifications.py` (mock webhook + mock SMTP)

Validate: «Test» button posts test message в Discord webhook URL (manual test); failed run триггерит ошибку → notification содержит traceback summary.

### Round 6 — F5 Pause / Resume (наиболее рискованная)

- `Runner.pause()` / `resume()` через signal/psutil (cross-OS abstraction в `core/process_control.py`)
- State.json marker `paused_at`
- Auto-cancel watcher (24h max pause)
- GUI Pause button + state machine
- Tests: unit (mock subprocess), integration (real ffmpeg + SIGSTOP под Linux/macOS; psutil.suspend под Windows)

Validate: real-ffmpeg run, pause во время encode segment 5 → resume через 30 сек → final QA report идентичен non-paused baseline; `state.json::paused_at` корректно очищается.

---

## Validation

```bash
make check                                  # ruff + mypy + полный pytest
make test-gui                               # offscreen Qt
make test-integration                       # реальный ffmpeg

PYTHONOPTIMIZE=2 make test                  # все hotfixes ещё уважаемы

# Round-specific
pytest tests/gui/test_accessibility.py -v
pytest tests/gui/test_theme_contrast.py -v
pytest tests/integration/test_platform_profiles.py -v
pytest tests/integration/test_pause_resume_real_ffmpeg.py -v
pytest tests/unit/test_notifications.py -v
pytest tests/unit/test_divergence_event.py -v

# Smoke
yt-uniq run sample.mp4 --profile tiktok_vertical
yt-uniq run sample.mp4 --profile soft --sample-phash light
QT_QPA_PLATFORM=offscreen yt-uniq-gui            # GUI smoke

# Manual
# 1. Discord webhook test in Settings → Test button → message arrives
# 2. VoiceOver/NVDA smoke на Run + Settings screens
# 3. Theme switch light↔dark → preflight badges + KPI pills перекрашиваются
# 4. Pause во время encode → spectrogram приостановлен → Resume → завершение
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| F5 `proc.send_signal(SIGSTOP)` ломает ffmpeg state на macOS / Linux (lost output buffer, fd leak) | Средняя | Tested на 3 OS до релиза. Fallback: pause только на segment boundary (не mid-segment), что не требует SIGSTOP — просто пауза orchestrator loop. |
| F5 Windows `psutil.Process.suspend()` race с подпроцессами ffmpeg (suspend родителя, дочерние работают) | Высокая | Использовать `psutil.Process(pid).suspend()` рекурсивно по дереву; alternative: ждать segment boundary. |
| F4 webhook URL может быть logged в crash.log (PII) | Средняя | В excepthook масируем поля `webhook_url` и `smtp.password` перед записью. |
| F4 keyring зависимость break'ит standalone install (system-keychain недоступен на headless Linux) | Высокая | Fallback на env var `YT_UNIQUIFIER_SMTP_PASSWORD`; если ни keyring, ни env — SMTP UI disabled с tooltip. |
| F2 pHash sampling добавляет 50ms × 1000 segments = +50 сек на длинном run | Средняя | Default `light` (every 4th segment) → +12 сек. Off-toggle в Settings. CLI `--sample-phash off/light/full`. |
| F3 `video.fit_aspect` pad_blur mode mis-renders на HDR source (gblur не HDR-aware) | Средняя | Документируем: pad_blur требует HDR→SDR conversion first; preflight check, если `keep_hdr=true` + `mode=pad_blur` — fail. |
| F3 5+ новых profiles умножают QA test matrix на 5-7× | Низкая | Integration test минимальный: 3-сек source × 7 profiles на Ubuntu only; на macOS только smoke (1 profile). |
| E1 accessibility sweep ломает существующие keyboard navigation users | Низкая | Только additive (новые shortcuts, новые AccessibleName); никаких удалений. |
| E3 migration helper на migration от старого `~/.config` пути теряет данные | Средняя | `copytree(...)` (не move). Старый путь оставляем как backup. CI test покрывает. |
| E12 `importlib.resources` под PyInstaller не находит `profiles/` если пакета нет в `datas` в `.spec` | Средняя | Добавить explicit datas-entry в `pyinstaller/yt-uniq-gui.spec`; integration test собирает frozen build на CI и проверяет presence файлов. |
| F4 Discord webhook rate limit (30 req/min) на batch runs | Низкая | Debounce: per-batch-end notification, не per-file. Конфиг `events: ["batch_completed"]` vs `["completed"]`. |

---

## Acceptance

### v0.7.0 ship-ready
- [ ] **F2** Live divergence indicator виден в Run + Batch screens (sparkline + KPI). RunEvent контракт документирован в `docs/architecture.md`. Sampling configurable.
- [ ] **F3** 5+ platform profiles шипятся, integration-test каждого зелёный. `video.fit_aspect` snapshot tests зелёные. Docs обновлены.
- [ ] **F4** Webhook (Discord / Slack / Telegram auto-detect) + SMTP работают. Settings UI + Test button. Notifications хук в orchestrator.
- [ ] **F5** Pause / Resume через GUI button работает на macOS + Linux + Windows. state.json marker корректно очищается. 24h auto-cancel.
- [ ] **F7** Auto-tune button в Run screen запускает calibrate + сохраняет tuned profile + switch'ит state.
- [ ] **E1** Все 10 screens + 7 widgets имеют `setAccessibleName` на interactive widgets. Primary CTAs имеют shortcuts (Ctrl+R, Esc, Ctrl+O, Ctrl+,, Ctrl+1..0). Regression test зелёный.
- [ ] **E3** `QStandardPaths.AppConfigLocation` используется везде. Migration helper работает (test).
- [ ] **E4** Theme switch light↔dark не leak'ит цвета. KPI pills + preflight badges берут tokens.
- [ ] **E6** sys.excepthook ловит unhandled, показывает dialog + Copy details. Crash.log пишется в CONFIG_DIR.
- [ ] **E12** `importlib.resources.files()` используется в 7 ранее-fragile screens. PyInstaller-build загружает profiles корректно (CI smoke).
- [ ] **C1–C7** Ноль `# type: ignore` в production code (`grep -rn "type: ignore" src/` пусто). mypy --strict зелёный.
- [ ] **WCAG-AA** regression: пары (fg, bg) для всех theme tokens проходят contrast ≥ 4.5.
- [ ] `make check` зелёный на CI matrix (3 OS × 2 Py).
- [ ] `make test-gui` зелёный.
- [ ] `make test-integration` зелёный.
- [ ] Smoke run на real 4K HDR source через GUI без regressions.
- [ ] Документация: `docs/gui.md` (новые shortcuts), `docs/profiles.md` (platform profiles раздел), `docs/architecture.md` (RunEvent contract обновлён).

---

**WAITING FOR CONFIRMATION**: Готов начать v0.7.0?

Варианты:
- **"yes"** / **"proceed"** — старт с Round 1 (plumbing + polish, низкий риск)
- **"start round N"** — пропустить вперёд (например, начать с F3 platform profiles)
- **"modify: …"** — изменить scope / приоритеты / split rounds иначе
- **"expand <раздел>"** — расписать детали по конкретной задаче (например, расписать SMTP keyring fallback, или показать конкретный YAML для tiktok_vertical, или дать диалог excepthook mockup)
- **"defer Fx"** — отложить конкретную фичу на v0.7.1 / v0.8.0
