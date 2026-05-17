# Spec 05 — GUI, Batch, Docs

> **Phase 5** · 2 дня · **Deps:** [03-segmenter](./03-segmenter-resume-metadata-preflight.md) и [04-qa-report](./04-qa-report.md)

## Goal

PyQt6-окно: пользователь выбирает входной файл, профиль, нажимает Run, видит прогресс, по завершении — ссылка «Open QA report».

`yt-uniq batch` обрабатывает директорию файлов последовательно с общим progress.

Полная документация: `architecture.md`, `profiles.md`, `filter_graph.md`, `youtube_targets.md`, новый `README.md`.

## Scope

**In:**

- `gui/app_pyqt.py` — главное окно, **тонкая обёртка** над `core/`.
- `gui/worker.py` — QThread, вызывает `runner.run(...)`, эмитит сигналы из RunEvent.
- `cli/cmd_batch.py` — batch-обработка директории.
- `cli/progress_view.py` — переиспользуемый rich-progress контроллер.
- `docs/architecture.md` — слои + диаграмма + поток данных.
- `docs/profiles.md` — YAML format + объяснение каждого transform.
- `docs/filter_graph.md` — как pipeline собирает filter_complex (ASCII-схема + примеры).
- `docs/youtube_targets.md` — preflight матрица + ссылки на YouTube help.
- `README.md` — переписать с нуля под новый инструмент.

**Not in:** иконки, тёмная/светлая тема, локализация (только английский), PyInstaller-bundle, web UI.

## Modules

### `gui/app_pyqt.py`

```python
from PyQt6.QtWidgets import (
    QMainWindow, QApplication, QFileDialog, QPushButton, QLabel,
    QComboBox, QProgressBar, QTextEdit, QVBoxLayout, QHBoxLayout, QWidget
)
from PyQt6.QtCore import Qt
from pathlib import Path

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("yt-uniquifier")
        self._build_ui()
        self.worker: Worker | None = None

    def _build_ui(self) -> None:
        # input row: label + path + browse
        # output row: label + path + browse
        # profile dropdown: scans src/yt_uniquifier/profiles/*.yaml
        # encoder dropdown: detect_encoders() + "auto"
        # run button (large)
        # cancel button (hidden until running)
        # progress bar
        # status label
        # log textedit (read-only, monospace)
        # qa report link (hidden until done)
        ...

    def on_run(self) -> None:
        # build Plan (через probe + load_profile + pick_encoder)
        # spawn Worker(plan, ...)
        # connect worker signals
        # disable inputs, show cancel button
        ...

    def on_progress(self, fraction: float, line: str) -> None: ...
    def on_log(self, line: str) -> None: ...
    def on_done(self, output_path: Path, qa_html_path: Path) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_cancel(self) -> None: ...

def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    sys.exit(app.exec())
```

**QSS-стиль** взять из легаси `src/main.py:20-130` как референс (знакомый dark-стиль), переписать под Qt6 (некоторые свойства устарели).

### `gui/worker.py`

```python
from PyQt6.QtCore import QThread, pyqtSignal
from pathlib import Path
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.runner import RunEvent, CancelToken

class Worker(QThread):
    progress = pyqtSignal(float, str)   # fraction, current message
    log = pyqtSignal(str)
    done = pyqtSignal(Path, Path)       # output_path, qa_html_path
    error = pyqtSignal(str)

    def __init__(self, plan: Plan, output: Path, work_dir: Path) -> None:
        super().__init__()
        self.plan = plan
        self.output = output
        self.work_dir = work_dir
        self.cancel_token = CancelToken()

    def run(self) -> None:
        try:
            # 1. вызывает orchestrator из cmd_run, передавая callback,
            #    который конвертит RunEvent → self.progress.emit / self.log.emit
            # 2. вызывает build_report + render_html
            # 3. self.done.emit(output, qa_html)
        except Exception as e:
            self.error.emit(str(e))

    def request_cancel(self) -> None:
        self.cancel_token.cancel()
```

**Архитектурное правило:** в Worker **нет** ffmpeg-логики. Worker только вызывает core-функции и проксирует события. Это лечит главный долг легаси (`src/main.py:251-377`), где ядро было вшито в QThread.

### `cli/cmd_batch.py`

```python
@app.command("batch")
def batch_cmd(
    inputs_dir: Path = typer.Argument(..., exists=True, dir_okay=True),
    profile: Path = typer.Option(..., "--profile"),
    output_dir: Path = typer.Option(..., "--out"),
    pattern: str = typer.Option("*.mp4", "--pattern"),
    fast_qa: bool = typer.Option(False, "--fast-qa"),
    continue_on_error: bool = typer.Option(True, "--continue-on-error/--stop-on-error"),
) -> None:
    """Process all files matching pattern in inputs_dir sequentially."""
```

Поведение:
- Сканирует `inputs_dir.glob(pattern)`.
- Для каждого: probe → preflight (если fail → skip, log warning) → run → qa.
- Общий прогресс: rich Progress с двумя строками — текущий файл (доли) + общий (n/total).
- Сводка в конце: успешно / пропущено / упало.
- Один файл — один `work_dir` под `output_dir/.work/<input_stem>/`.
- Конкурентность: 1 (GPU моно). Опция `--workers N` отложена до v1.1.

### `cli/progress_view.py`

```python
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn

def make_run_progress() -> Progress:
    """Single bar + speed + ETA — для cmd_run."""

def make_batch_progress() -> Progress:
    """Two bars: overall (files done/total) + current file (fraction)."""
```

Используется в `cmd_run.py` (Spec 03), `cmd_batch.py` (этот спек), `cmd_qa.py` (Spec 04).

### `docs/architecture.md`

Содержит:
1. Layer diagram (из мастер-плана).
2. Data flow для одного входа.
3. Описание модулей `core/` по фазам.
4. Ссылки на каждый spec.

### `docs/profiles.md`

- Структура YAML.
- Описание всех 9 transforms с диапазонами параметров и эффектом.
- Готовые профили (`soft/medium/aggressive/legacy_ab`) — что включают и зачем.
- Howto: написать свой профиль.

### `docs/filter_graph.md`

- Принцип «один transform = один FilterChain».
- LabelAllocator.
- ASCII-пример сборки для `medium`.
- Что делает Pipeline дополнительно (encoder args, mapping, metadata).
- Особый случай `blend_b` с дополнительным -i.

### `docs/youtube_targets.md`

- Полная preflight матрица из Spec 03.
- Ссылки на:
  - YouTube recommended upload encoding settings.
  - Loudness recommendations.
  - Bitrate brackets.
- Что значит каждый код finding и как чинить.

### `README.md` (новый)

Структура:

```markdown
# yt-uniquifier

> Production-grade re-encoder for owned/licensed video content with controlled micro-transforms.
> Intended for legitimate re-upload scenarios (your own content, re-cuts, fair-use derivatives).

## What it does
[краткий список фич]

## What it is NOT
[явный disclaimer: не для обхода чужих авторских прав]

## Install
[pip / pipx инструкции, ffmpeg req, optional fpcalc/libvmaf]

## Quickstart
[пара команд: probe → preflight → run]

## Profiles
[ссылка на docs/profiles.md]

## CLI reference
[список команд с одной строкой описания]

## GUI
[скриншот, install [gui], запуск]

## Architecture
[ссылка на docs/architecture.md]

## License
MIT
```

## Acceptance

```bash
# 1. GUI launches
pip install -e ".[gui]"
yt-uniq-gui    # или python -m yt_uniquifier.gui.app_pyqt
# Открывается окно. Выбираю input + profile + output, жму Run.
# Progress bar заполняется, по завершении появляется кнопка "Open QA report".

# 2. Batch
yt-uniq batch ~/movies/inputs/ --profile ...medium.yaml --out ~/movies/outputs/
# Обрабатывает все *.mp4 последовательно, печатает сводку.

# 3. Docs
ls docs/
# architecture.md  filter_graph.md  profiles.md  youtube_targets.md

# 4. README
grep -l "obfuscat\|content id evasion\|bypass copyright" README.md
# не должно быть таких терминов — явный disclaimer

# 5. Cancel from GUI
# Запускаю длинный файл, жму Cancel → процесс завершается за <10 сек, GUI в idle state.

# 6. Resume from GUI
# Запускаю длинный файл, закрываю окно, открываю снова с теми же input/output/work_dir,
# жму Run → продолжает с последнего сегмента.
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_worker_signals.py` | mock core.run, проверяем что RunEvent корректно превращаются в signals |
| Unit | `tests/unit/test_worker_cancel.py` | request_cancel → CancelToken проставлен |
| Unit | `tests/unit/test_progress_view.py` | make_batch_progress создаёт Progress с двумя tasks |
| Integration | `tests/integration/test_batch_cli.py` | batch на дир с 2-3 короткими клипами, проверка outputs |
| Integration | `tests/integration/test_batch_continue_on_error.py` | один поломанный файл в середине → остальные обрабатываются |
| Smoke | `tests/smoke/test_gui_imports.py` | `import yt_uniquifier.gui.app_pyqt` не падает (если PyQt6 установлен) |
| Manual | runbook | визуальная проверка GUI: progress, log scroll, QA report link |

GUI-тесты через `pytest-qt` опциональны (на CI с PyQt6 может быть нестабильно) — основная логика покрыта Worker unit-тестами.

## Risks

- **PyQt6 на CI macOS GitHub runner** иногда требует QT_QPA_PLATFORM=offscreen. Smoke-тест проверяет только импорт.
- **Worker сигналы через QThread boundary** — все payload-типы должны быть PyQt-safe (str, int, float, Path). pydantic-модели сериализуются в dict перед emit, если нужно.
- **GUI и batch не должны дублировать orchestration логику cmd_run.** Решение: вынести оркестратор в `core/orchestrator.py::run_full(plan, output, work_dir, on_event)` ещё в Spec 03 → GUI Worker и cmd_run/cmd_batch одинаково его дёргают.
  - Если в Spec 03 это не было сделано — Spec 05 рефакторит cmd_run, перенося логику в core/orchestrator.py.
- **Документация может разойтись с кодом.** Митигация: docs/profiles.md содержит примеры, которые тестируются `tests/integration/test_run_short_clip.py` (загружает реальные YAML профили).

## Hand-off (release v1.0)

После Phase 5:
- CLI + GUI + batch + docs готовы.
- CI зелёный на ubuntu+macos для python 3.11 и 3.12.
- README не содержит формулировок «evasion / bypass / Content ID circumvention».
- Готово к `git tag v0.1.0` и публикации (если решено).

## Что отложено в v1.1

См. секцию «Вне скоупа v1» в [мастер-плане](/Users/admin/.claude/plans/snoopy-sprouting-kay.md):

- Параллельная обработка сегментов на CPU.
- Авто-итеративная подстройка интенсивности по pHash-target.
- HDR → SDR tonemap.
- Image-based subtitles (только warning+skip в v1).
- Web UI / REST API.
- Распределённая batch-обработка.
- PyInstaller one-file сборка.
