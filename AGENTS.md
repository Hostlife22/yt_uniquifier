# Repository Guidelines

## Project Structure & Module Organization

This Python 3.11+ project uses a `src` layout. Application code lives in
`src/yt_uniquifier/`: `core/` contains UI-independent processing, `cli/` the Typer
commands, `gui/` the PyQt6 desktop application, and `web/` the FastAPI interface.
Shipped YAML profiles are under `profiles/`. Tests mirror behavior by scope in
`tests/unit`, `tests/integration`, `tests/gui`, `tests/smoke`, `tests/contracts`,
`tests/property`, and `tests/visual`. Documentation, implementation plans, utility
scripts, and packaging definitions live in `docs/`, `specs/`, `tools/`, and
`pyinstaller/` respectively.

Keep business logic in `core/`; CLI, GUI, and web layers should call core APIs rather
than duplicate orchestration. Preserve the legitimate-use framing in user-facing text.

## Build, Test, and Development Commands

The `Makefile` is the canonical developer interface:

- `make dev` creates `.venv` and installs editable development and GUI dependencies.
- `make dev-min` installs a lighter CLI-only development environment.
- `make lint` runs Ruff; `make typecheck` runs strict mypy on `src/`.
- `make test-unit` runs fast tests; `make test-integration` requires `ffmpeg`.
- `make check` runs all required quality gates before review.
- `make build-wheel` builds a wheel in `dist/`; `make build` packages the GUI.

Run focused tests with `.venv/bin/pytest tests/unit/test_models.py -q` or
`.venv/bin/pytest -k label_allocator -q`. `ffmpeg` and `ffprobe` must be on `PATH`.

## Coding Style & Naming Conventions

Use four-space indentation, Python type annotations, and lines no longer than 100
characters. Ruff enforces `E`, `F`, `W`, import sorting, Bugbear, pyupgrade, and
simplification rules; use `make lint-fix` for safe fixes. Follow standard Python
naming: `snake_case` for modules/functions, `PascalCase` for classes, and
`UPPER_CASE` for constants. Public core code must satisfy strict mypy.

## Testing Guidelines

Use pytest and name files `test_<behavior>.py`. Mark real-ffmpeg tests with
`@pytest.mark.integration`; use existing fixtures such as `tiny_clip` and
`isolated_cache`. Add regression tests for fixes and snapshot tests for transform
filter graphs or stable contracts. CI enforces at least 80% coverage for `core/`.

## Commit & Pull Request Guidelines

Use Conventional Commits, for example `fix(core/segmenter): preserve resume state`.
Allowed types include `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`,
`chore`, and `build`. Explain why in the body when needed. PRs must describe the
change, link issues (`Closes #123`), pass `make check`, and include screenshots for
GUI changes. Stable API, CLI, event, or profile changes require an approved RFC,
updated contract snapshots, documentation, and a `CHANGELOG.md` entry; see
`CONTRIBUTING.md` and `docs/api-contracts.md`.
