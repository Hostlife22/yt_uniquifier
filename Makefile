# yt-uniquifier — shortcuts for common dev / install / build tasks.
#
# Usage:
#   make              show this help
#   make <target>     run a specific target
#
# Cross-platform note: works on macOS / Linux. Windows users either use
# WSL / git-bash, or run the underlying commands directly (see each
# target's recipe).

# ---- configuration ---------------------------------------------------
PYTHON      ?= python3.12
VENV        ?= .venv
PIP         := $(VENV)/bin/pip
PY          := $(VENV)/bin/python
RUFF        := $(VENV)/bin/ruff
MYPY        := $(VENV)/bin/mypy
PYTEST      := $(VENV)/bin/pytest
YT_UNIQ     := $(VENV)/bin/yt-uniq
YT_UNIQ_GUI := $(VENV)/bin/yt-uniq-gui
QT_OFFSCREEN := QT_QPA_PLATFORM=offscreen

# ---- meta ------------------------------------------------------------
.DEFAULT_GOAL := help

.PHONY: help
help:   ## Show this help.
	@echo "yt-uniquifier — make targets:"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Tip: most targets assume \$$VENV=$(VENV). Override with VENV=/path/to/venv."

# ---- environment -----------------------------------------------------
$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

.PHONY: venv
venv: $(VENV)/bin/activate  ## Create Python virtualenv at $(VENV).

.PHONY: install
install: venv  ## Install package + [gui] extra (production).
	$(PIP) install -e ".[gui]"

.PHONY: dev
dev: venv  ## Install package + [dev,gui] extras (USE_LOCK=1 → install from requirements-lock.txt).
ifeq ($(USE_LOCK),1)
	$(PIP) install --require-hashes -r requirements-lock.txt
	$(PIP) install -e ".[dev,gui]" --no-deps
else
	$(PIP) install -e ".[dev,gui]"
endif

.PHONY: dev-min
dev-min: venv  ## Install [dev] only (no PyQt6/WebEngine — CLI work).
	$(PIP) install -e ".[dev]"

.PHONY: lock
lock: venv  ## Regenerate requirements-lock.txt via uv pip compile (Python 3.11 + 3.12 ABIs).
	# v1.0.1 Task 7: pin the dev + gui dependency closure so CI installs
	# the same versions every run. Hash mode means a tampered registry
	# can't silently substitute a malicious wheel. Regenerate this file
	# whenever pyproject.toml dependency ranges change; commit the diff.
	@which uv >/dev/null 2>&1 || { \
		echo "uv not found on PATH — install via 'pipx install uv' or 'pip install uv'"; \
		exit 1; \
	}
	uv pip compile pyproject.toml \
		--extra dev --extra gui \
		--python-version 3.11 \
		--universal \
		--generate-hashes \
		--output-file requirements-lock.txt
	@echo "Wrote requirements-lock.txt — review the diff before committing."

# ---- quality gates ---------------------------------------------------
.PHONY: lint
lint:  ## Run ruff (style + import sort + bugbear). Scope matches CI.
	$(RUFF) check .

.PHONY: lint-fix
lint-fix:  ## Auto-fix ruff issues where possible.
	$(RUFF) check . --fix

.PHONY: typecheck
typecheck:  ## Run mypy --strict on src/.
	$(MYPY) src/yt_uniquifier

.PHONY: test
test:  ## Run full pytest suite (~2–20 min by extras/hardware; includes real FFmpeg).
	$(QT_OFFSCREEN) $(PYTEST) -q

.PHONY: test-unit
test-unit:  ## Run unit tests only (~10s, no real ffmpeg).
	$(QT_OFFSCREEN) $(PYTEST) tests/unit/ -q

.PHONY: test-gui
test-gui:  ## Run GUI tests (headless via QT_QPA_PLATFORM=offscreen).
	$(QT_OFFSCREEN) $(PYTEST) tests/unit/test_gui_*.py tests/smoke/test_gui_full_launch.py -q

.PHONY: test-integration
test-integration:  ## Run integration tests (real ffmpeg required).
	$(QT_OFFSCREEN) $(PYTEST) tests/integration/ -q

.PHONY: test-visual
test-visual:  ## Run GUI screenshot regression (Linux-offscreen only).
	$(QT_OFFSCREEN) $(PYTEST) tests/visual/ -m visual -q

.PHONY: test-visual-update
test-visual-update:  ## Refresh GUI screenshot baselines (intentional change).
	UPDATE_VISUAL_BASELINES=1 $(QT_OFFSCREEN) $(PYTEST) tests/visual/ -m visual -q

.PHONY: check
check: lint typecheck test  ## All quality gates: ruff + mypy + full pytest.
	@echo "✓ all checks passed"

# ---- run -------------------------------------------------------------
.PHONY: gui
gui:  ## Launch yt-uniq-gui (desktop app).
	$(YT_UNIQ_GUI)

.PHONY: cli
cli:  ## Show yt-uniq CLI help.
	$(YT_UNIQ) --help

.PHONY: probe-encoders
probe-encoders:  ## List ffmpeg encoders detected on this machine.
	$(YT_UNIQ) probe --encoders

# ---- packaging -------------------------------------------------------
.PHONY: build
build: venv  ## Build desktop binary via PyInstaller (dist/yt-uniq-gui.app on macOS).
	$(PIP) install --quiet pyinstaller
	$(PY) -m PyInstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm

.PHONY: build-wheel
build-wheel: venv  ## Build pip-installable wheel into dist/.
	$(PIP) install --quiet build
	$(PY) -m build --wheel

# ---- maintenance -----------------------------------------------------
.PHONY: reset-cache
reset-cache:  ## Remove encoder + keyframe + work caches (force re-detect).
	rm -rf ~/.cache/yt_uniquifier/encoders.json
	rm -rf ~/.cache/yt_uniquifier/keyframes
	@echo "Cleared ~/.cache/yt_uniquifier/{encoders.json,keyframes}"

.PHONY: clean
clean:  ## Remove build artefacts (dist/, build/, __pycache__, .pytest_cache).
	rm -rf dist/ build/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	@echo "Cleaned build + cache directories"

.PHONY: distclean
distclean: clean  ## clean + remove $(VENV) (full reset; re-run `make dev` after).
	rm -rf $(VENV)
	@echo "Removed $(VENV). Run \`make dev\` to recreate."
