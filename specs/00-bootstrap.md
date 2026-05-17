# Spec 00 — Bootstrap

> **Phase 0** · 0.5 дня · **No deps**

## Goal

Проект собирается через `pipx install -e .`, команда `yt-uniq --help` печатает root help-меню typer, CI зелёный на пустом тесте.

## Scope

**In:**

- Удаление старого `src/`, `utils/`, `requirements.txt`, `assets/cover_*.png`.
- Переименование репо-папки `Video-Deduplicator` → `yt-uniquifier`.
- `pyproject.toml` (PEP 621) с пинованными версиями и extras.
- Каркас директорий `src/yt_uniquifier/`, `tests/`, `docs/`, `.github/workflows/`.
- Минимальная typer-команда (`yt-uniq --help` работает).
- `.github/workflows/ci.yml`: ruff + pytest на ubuntu и macos.

**Not in:** функциональность — только каркас. Никаких probe/transforms/pipeline.

## Pre-step (один раз перед удалением)

Репо не под git. Прежде чем удалять — `git init && git add -A && git commit -m "baseline: legacy Video-Deduplicator prototype"`. Без этого старый код потеряется без следа.

## Modules

### `pyproject.toml`

```toml
[project]
name = "yt-uniquifier"
version = "0.1.0a0"
requires-python = ">=3.11"
dependencies = [
    "typer~=0.12",
    "pydantic~=2.7",
    "rich~=13.7",
    "pyyaml~=6.0",
    "imagehash~=4.3",
    "Pillow~=10.3",
    "structlog~=24.1",
    "jinja2~=3.1",
]

[project.optional-dependencies]
gui = ["PyQt6~=6.7"]
qa  = ["pyacoustid~=1.3"]
dev = ["pytest~=8.2", "pytest-cov~=5.0", "ruff~=0.5", "mypy~=1.10"]

[project.scripts]
yt-uniq = "yt_uniquifier.cli.app:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "integration: tests that invoke real ffmpeg",
    "smoke: minimal CI sanity tests",
]
```

### `src/yt_uniquifier/cli/app.py`

```python
import typer

app = typer.Typer(no_args_is_help=True, help="Re-encode owned/licensed video with controlled micro-transforms for YouTube re-upload.")

@app.command()
def version() -> None:
    """Print version."""
    from yt_uniquifier import __version__
    typer.echo(__version__)
```

### `src/yt_uniquifier/__init__.py`

```python
__version__ = "0.1.0a0"
```

### `src/yt_uniquifier/__main__.py`

```python
from yt_uniquifier.cli.app import app
if __name__ == "__main__":
    app()
```

### `.github/workflows/ci.yml`

- Matrix: `os: [ubuntu-latest, macos-latest]`, `python: ["3.11", "3.12"]`.
- Steps: checkout → setup-python → install ffmpeg via apt/brew → `pip install -e ".[dev]"` → `ruff check .` → `pytest -q`.

### Каркас директорий (пустые `__init__.py`)

```
src/yt_uniquifier/{core,core/transforms,core/qa,core/utils,cli,gui}/__init__.py
src/yt_uniquifier/profiles/   # пустая, заполняется в Spec 02
tests/{unit,integration,smoke}/__init__.py
docs/                          # пустая, заполняется в Spec 05
```

### `tests/smoke/test_version.py`

```python
from typer.testing import CliRunner
from yt_uniquifier.cli.app import app

def test_version_command():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
```

## Acceptance

```bash
cd /Users/admin/Projects/MyProjects/youtube/yt-uniquifier   # после переименования
pip install -e ".[dev]"
yt-uniq --help        # печатает root help
yt-uniq version       # печатает 0.1.0a0
ruff check .          # 0 errors
pytest -q             # 1 passed
```

CI workflow зелёный на push в любую ветку.

## Tests

| Уровень | Файл | Что |
|---|------|------|
| Smoke | `tests/smoke/test_version.py` | `yt-uniq version` отвечает корректно |

## Risks

- Hatchling требует структуры `src/<package>/` — она у нас уже такая, но пути нужно проверить при `pip install -e .`.
- macOS GitHub runner может не иметь ffmpeg по умолчанию — `brew install ffmpeg` занимает ~2 мин, готово.

## Hand-off в Spec 01

После Phase 0:
- Импорт `from yt_uniquifier import core, cli, gui` работает.
- typer-app существует и регистрируется как entry point.
- Все будущие команды добавляются через `app.add_typer(...)` или `@app.command()`.
