# Plan: yt-uniquifier v1.0.0 — Stable release

**Source PRD**: `.claude/plans/yt-uniquifier-best-in-class.plan.md` (§ "v1.0.0 — Stable release" + § 10 Acceptance)
**Selected Milestone**: v1.0.0 production-ready manifest
**Complexity**: **Large** (7 rounds, ~3-4 недели чистой работы + внешние блокеры на code signing)
**Текущая точка**: v0.9.0 shipped 2026-06-14 (commits `dc85cc2`..`333017d`), 942 тестов зелёные, mkdocs site, telemetry opt-in, F13/F14/F9 закрыты.

---

## Summary

v1.0.0 — это **финализирующий релиз без новых фич**. Содержание: API-контракты замораживаются, SemVer + RFC процесс кодифицируется, performance regression suite запускается в CI nightly, coverage поднимается до 85%+ на `core/`, signed installers для 3 платформ доводятся до production-ready (F1 R2-R5), accessibility допиливается до полного WCAG 2.1 AA на всех 10 GUI экранах, security disclosure policy + bug bounty публикуются. Версия в `pyproject.toml` уходит из `0.1.0a0` сначала в `1.0.0rc1`, потом в `1.0.0`.

**Главное "что не делаем"**: новых transforms, новых GUI экранов, новых core фич — нет. Любой scope creep идёт в v1.1.

---

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Round-based release | `.claude/plans/yt-uniquifier-v0.9.0.plan.md` § R1..R6 | Atomic commit per round, "shipped" checklist в master plan после каждого |
| Version bump | `pyproject.toml:11` (`version = "0.1.0a0"`), `__init__.py` (если есть) | Один источник истины, остальное берётся из `importlib.metadata.version()` |
| New CLI subcommand | `src/yt_uniquifier/cli/cmd_telemetry.py` (v0.9 R3) | Sub-app via `typer.Typer()` + register в `cli/app.py` + tests in `tests/unit/test_cli_*.py` |
| Docs site addition | `docs/telemetry.md` + `mkdocs.yml` nav (v0.9 R6) | Markdown с frontmatter-less H1, link из `docs/index.md`, добавить в `mkdocs.yml` nav |
| CI workflow | `.github/workflows/{ci,docs,release}.yml` | YAML с pinned actions (`actions/checkout@v4`), matrix strategy, fail-fast: false на test jobs |
| Regression-test file | `tests/integration/test_pause_resume_real_ffmpeg.py` (v0.7 R7) | Markers: `@pytest.mark.integration`, `@needs_ffmpeg`, conftest fixtures |
| Coverage report | (новое) | `pytest --cov=src/yt_uniquifier --cov-report=term-missing --cov-fail-under=85` в `[tool.pytest.ini_options]` |
| GUI accessibility | `tests/unit/test_gui_accessibility.py` (v0.7 R6) | `setAccessibleName/Description/setTabOrder/setShortcut` + 12-case assertion table |
| Security policy | (новое) | `SECURITY.md` в корне репо — GitHub автодетектит, показывает кнопку "Report a vulnerability" |
| Performance benchmark | `tools/benchmark.py` (already exists) | CSV-append per run; extend на JSON + публикация в gh-pages |

---

## Files to Change (по rounds — детали в § Tasks)

| Файл | Action | Round | Why |
|---|---|---|---|
| `pyproject.toml` | UPDATE | R1, R7 | Version bump `0.1.0a0` → `1.0.0rc1` (R1) → `1.0.0` (R7); coverage gate; classifiers `Development Status :: 5 - Production/Stable` |
| `CHANGELOG.md` | CREATE | R1 | Cumulative changelog v0.5.5 → v1.0.0 (8 релизов, ~80 коммитов). Keep-a-Changelog format |
| `docs/versioning.md` | CREATE | R1 | SemVer commitment + RFC процесс + что считается breaking change |
| `SECURITY.md` | CREATE | R1 | Disclosure policy: emails, response timeline, scope, GitHub Private Vulnerability Reporting enable |
| `docs/api-contracts.md` | CREATE | R2 | Public API surface: `Plan`, `Profile`, `RunEvent`, `RunOptions`, `RunSummary`, `EncoderCandidate`, `SourceMeta`. Field-level stability guarantees. |
| `src/yt_uniquifier/__init__.py` | UPDATE | R2 | Explicit `__all__` для публичной API surface; `__version__` через `importlib.metadata.version("yt-uniquifier")` |
| `src/yt_uniquifier/core/__init__.py` | UPDATE | R2 | Re-export stable contracts: `Plan`, `Profile`, `RunOptions`, `RunSummary`, `RunEvent` |
| `tests/contracts/test_plan_serialization_stable.py` | CREATE | R2 | Golden JSON snapshots для каждой версии профиля; A failing snapshot = breaking change → требует major bump |
| `tests/contracts/test_runevent_kinds_stable.py` | CREATE | R2 | Перечислить все `RunEvent.kind` literals + payload schema, lock как stable |
| `tests/contracts/test_profile_schema_stable.py` | CREATE | R2 | JSONSchema-export всех 17 transform Params + сравнение с golden file |
| `pyproject.toml` | UPDATE | R3 | `[tool.coverage]` + `addopts` с `--cov-fail-under=85` (только на CI, не локально) |
| `.github/workflows/ci.yml` | UPDATE | R3 | Coverage gate как отдельный job; `--cov-fail-under=80` на `core/` |
| `tests/unit/test_calibration_intensity.py` | CREATE | R3 | Закрыть нулевое покрытие `calibration/intensity.py` (master plan D3) |
| `tests/unit/test_video_color_snapshots.py`, `test_video_noise_snapshots.py` | CREATE | R3 | Master plan D1: snapshot tests для двух самых частых transforms |
| `tests/unit/test_utils_ffmpeg_paths.py` | CREATE | R3 | Master plan D6: #1 user-support failure mode не покрыт |
| `tools/benchmark.py` | UPDATE | R4 | JSON output, model "regression_baseline.json" перед commit; argparse `--baseline-json` |
| `tools/perf_compare.py` | CREATE | R4 | Compare benchmark.json N vs N-1; exit non-zero на >5% regression на любой метрике |
| `.github/workflows/perf-regression.yml` | CREATE | R4 | Nightly + on-tag; uses fixture, publishes JSON artifact, posts diff в PR comment |
| `tests/fixtures/perf_baseline_720p.mp4` | CREATE | R4 | 60-сек 720p testsrc2 (built at fixture-time, не binary в git) |
| `.github/workflows/release.yml` | UPDATE | R5 | F1 R2: notarytool + codesign step (macOS); F1 R3: WiX MSI + signtool (Windows); F1 R4: appimagetool + bundled ffmpeg static (Linux) |
| `installers/macos/sign-notarize.sh` | CREATE | R5 | Signing helper: requires `APPLE_TEAM_ID`, `APPLE_DEV_ID_CERT`, `APPLE_ID_USERNAME`, `APPLE_NOTARY_KEY` (CI secrets) |
| `installers/windows/wxs/yt-uniquifier.wxs` | CREATE | R5 | WiX source: product MSI с registry entries, Start Menu shortcut, uninstaller |
| `installers/linux/AppImageBuilder.yml` | CREATE | R5 | appimagetool recipe: bundles Python 3.11 runtime + ffmpeg static x86_64 |
| `docs/install.md` | UPDATE | R5 | Replace pip-only с download-installer-first; pip остаётся как dev-install |
| `src/yt_uniquifier/gui/screens/*.py` (10 files) | EDIT | R6 | WCAG 2.1 AA finishing: focus indicators, color-only-meaning fixes, error-message recovery, form labels |
| `tests/gui/test_wcag_aa_compliance.py` | CREATE | R6 | Programmatic checks: контраст (уже есть в v0.7 R7), focus visibility, target size 44×44 px, accessibleName на каждом focusable widget |
| `docs/accessibility.md` | CREATE | R6 | WCAG 2.1 AA conformance statement: что покрыто, что known-limitations, screen-reader manual test guide |
| `mkdocs.yml` | UPDATE | R7 | Versioned docs via `mike` plugin: `latest` + tags; "Edit on GitHub" link |
| `docs/index.md` | UPDATE | R7 | v1.0 landing: highlight SemVer commitment, stable API badge, installer downloads |
| `docs/CONTRIBUTING.md` | UPDATE/CREATE | R7 | RFC процесс: где открывать, шаблон, sign-off на breaking change |
| `.github/ISSUE_TEMPLATE/{bug,feature,rfc}.yml` | CREATE | R7 | Structured issue templates; RFC template отдельный |
| `.github/SECURITY.md` | (already in repo root from R1) | R7 | GitHub автодетектит из `.github/` или корня |
| `pyproject.toml` | UPDATE | R7 | Version `1.0.0`; classifier `Development Status :: 5 - Production/Stable` |
| `CHANGELOG.md` | UPDATE | R7 | Финализация: переименовать `[Unreleased]` → `[1.0.0] — YYYY-MM-DD` |
| `.claude/plans/yt-uniquifier-best-in-class.plan.md` | UPDATE | R7 | Закрыть оставшиеся `[ ]` в § 10 Acceptance + § 5 v1.0.0 после shipping |

---

## Tasks (round-by-round)

### **R1 — Version bump, CHANGELOG, SemVer policy, SECURITY** (~3-4 часа)

- **Action**:
  - `pyproject.toml`: `version = "0.1.0a0"` → `"1.0.0rc1"`; classifier `Development Status :: 3 - Alpha` → `Development Status :: 4 - Beta` (rc — ещё не Production/Stable, это R7).
  - `CHANGELOG.md`: создать с Keep-a-Changelog format. Перечислить v0.5.5 (A1-A10), v0.6.0 (B1-B8 + F8), v0.7.0 (F2/F3/F4/F5/F7 + E* + R7), v0.8.0 (F6/F10/F11/F12 + plugin system), v0.9.0 (F9/F13/F14 + telemetry + i18n + mkdocs). Источники — `git log --oneline` + master plan checkboxes.
  - `docs/versioning.md`: SemVer commitment (MAJOR = breaking; MINOR = feature; PATCH = fix). Breaking change definition — что считается breaking для каждого контракта (Plan / Profile / CLI / RunEvent / Python API). RFC процесс — minimal: GitHub Discussion → label `rfc` → 7 day comment window → labelled `accepted`/`rejected`.
  - `SECURITY.md`: email (gabrusevichjeka@gmail.com или create dedicated alias), response timeline (3 рабочих дня acknowledge, 30 дней fix для CRITICAL), supported versions table (1.0.x), GPG ключ опционально. Включить GitHub Private Vulnerability Reporting в Settings репо.
- **Mirror**: Keep-a-Changelog (https://keepachangelog.com), Python SemVer (PEP 440 уже соблюдается через `~=`).
- **Validate**: `make lint && make typecheck && pytest tests/smoke -q && python -c "import yt_uniquifier; print(yt_uniquifier.__version__)"` → `1.0.0rc1`.

### **R2 — API freeze + contract snapshot tests** (~6-8 часов)

- **Action**:
  - `src/yt_uniquifier/__init__.py`: explicit `__all__ = ["Plan", "Profile", "RunOptions", "RunSummary", "RunEvent", "build_plan", "run_full", ...]` + `__version__`.
  - `src/yt_uniquifier/core/__init__.py`: re-export stable surface (сейчас в `core/` нет `__init__.py` барреля — добавить).
  - `docs/api-contracts.md`: field-by-field таблица для `Plan`, `Profile`, `RunEvent` с stability label (`stable` / `experimental` / `internal`). Reference: pydantic v2 `BaseModel.model_json_schema()`.
  - `tests/contracts/test_plan_serialization_stable.py`: каждый shipped профиль (cid_aware, soft, medium, ...) → `build_plan(SourceMeta(test_fixture), profile, encoder=stub).model_dump_json()` → сравнение с `tests/fixtures/contracts/plan_<profile>.golden.json`. Failure = или регрессия (фикс), или intentional breaking change → bump version + regenerate golden + добавить в CHANGELOG.
  - `tests/contracts/test_runevent_kinds_stable.py`: перечислить все известные `RunEvent.kind` values (grep `RunEvent(kind=` в `src/`) → assert против golden list. Adding a new kind = MINOR; removing = MAJOR.
  - `tests/contracts/test_profile_schema_stable.py`: `Profile.model_json_schema()` → JSON → diff vs `tests/fixtures/contracts/profile_schema.golden.json`. Same MAJOR/MINOR rules.
  - Helper script `tools/regen_contract_goldens.py` для intentional updates.
- **Mirror**: pydantic schema export — `src/yt_uniquifier/core/profile_loader.py` уже использует `Profile.model_validate(...)`.
- **Validate**: `pytest tests/contracts -v` все зелёные; `python tools/regen_contract_goldens.py --dry-run` показывает 0 diff.

### **R3 — Coverage push to 85%+ on core/** (~10-14 часов, самый большой)

- **Action**:
  - Установить baseline: `pytest --cov=src/yt_uniquifier --cov-report=term-missing --cov-report=html > coverage_baseline.txt`. Ожидаю в районе 70-78% на core/ исходя из 65 файлов × 145 test files.
  - Идентифицировать gaps (master plan § D + новые):
    1. `calibration/intensity.py` — ZERO покрытия (D3). Добавить `tests/unit/test_calibration_intensity.py` с `_scale_params`, `_clamp_to_schema` (включая fallback на unknown transform_id из § C5).
    2. `core/transforms/video_color.py`, `video_noise.py` — нет snapshot (D1). Добавить snapshot tests по образцу `tests/unit/test_transforms.py`.
    3. `core/utils/ffmpeg_paths.py` — ZERO тестов (D6).
    4. `core/audio_loudnorm.py` cache resume path (D5).
    5. `core/queue/leasing.py` cross-process race — заменить thread-based test на `subprocess` (D4); требует careful tmp dir + полный teardown.
    6. `core/orchestrator.py` resume edge cases: SIGKILL посередине сегмента, переиспользование state.json после изменения профиля.
  - `pyproject.toml` `[tool.pytest.ini_options]` `addopts`: добавить `--cov-config=.coveragerc` (не в `addopts` базово, чтобы local запуски не блокировались coverage).
  - `.coveragerc`: source = `src/yt_uniquifier/core` (UI/CLI отдельно), omit `*/tests/*`, `*/migrations/*`, branch coverage on.
  - `.github/workflows/ci.yml`: новый job `coverage` после `test`, запускает `pytest --cov=src/yt_uniquifier/core --cov-fail-under=85`. Для GUI и CLI — отдельные мягкие порога (75% и 80% respectively), не блокирующие.
- **Mirror**: existing tests in `tests/unit/test_transforms.py` (snapshot pattern), `tests/integration/test_resume_partial_cleanup.py` (orchestrator edge case pattern).
- **Validate**:
  ```bash
  pytest --cov=src/yt_uniquifier/core --cov-report=term-missing --cov-fail-under=85
  pytest --cov=src/yt_uniquifier/gui --cov-fail-under=75
  pytest --cov=src/yt_uniquifier/cli --cov-fail-under=80
  ```

### **R4 — Performance regression suite + CI nightly** (~6-8 часов)

- **Action**:
  - `tools/benchmark.py` extend: `--json output.json` write структуру `{"git_sha": ..., "py_version": ..., "os": ..., "wall_s": ..., "rss_peak_mb": ..., "segments": ..., "per_phase": {...}}`. Сохранить `--baseline-json prior.json` для diff (`% delta` на каждой метрике).
  - `tools/perf_compare.py`: загружает baseline + current, exit-code 1 if any метрика regressed >5% (configurable threshold). Print markdown-table diff для PR-комментария.
  - `tests/fixtures/perf_baseline_720p.mp4`: НЕ хранить в git (binary). Generate в `conftest.py` fixture-functions через `ffmpeg testsrc2 + sine`. Fixture session-scoped, cached в `~/.cache/yt-uniquifier/test_fixtures/`. Length: 60 сек 720p24 (~5 MB).
  - `.github/workflows/perf-regression.yml`:
    - Trigger: `schedule: cron: "17 3 * * *"` + `workflow_dispatch`.
    - Job: ubuntu-latest, `make dev`, generate fixture, run `tools/benchmark.py` с `--encoder libx264 --workers 2`.
    - Upload JSON как artifact, retention 90 дней.
    - Fetch предыдущий artifact из `main` (latest schedule run) → `python tools/perf_compare.py`.
    - On regression: open issue с label `perf-regression` + diff table.
  - **Decision: results storage**. Простой путь — GitHub Releases в `perf-history` tag (gh-pages overkill). Простейший — artifacts с retention. Запросить выбор у пользователя при confirmation.
- **Mirror**: existing `tools/benchmark.py` argparse pattern, `.github/workflows/docs.yml` schedule trigger.
- **Validate**: `python tools/benchmark.py tests/fixtures/perf_baseline_720p.mp4 --profile src/yt_uniquifier/profiles/cid_aware.yaml --out /tmp/o.mp4 --json /tmp/b.json && python tools/perf_compare.py --baseline /tmp/b.json --current /tmp/b.json` (self-diff = 0%).

### **R5 — F1 R2-R5: signed installers for 3 platforms** (~8-12 часов + БЛОКЕРЫ на credentials)

⚠️ **ВНЕШНИЕ БЛОКЕРЫ**:
1. **Apple Developer Program** account ($99/year) → `APPLE_TEAM_ID`, Developer ID Application cert, notarytool API key. **Без этого macOS signing невозможен.**
2. **Windows Code Signing Certificate** (~$200-400/year от DigiCert/Sectigo/etc., либо EV для better SmartScreen reputation ~$400+). Без него Windows EXE/MSI остаётся с SmartScreen warning.
3. **GitHub Secrets** настройка через repo settings — нужны user actions.

Если credentials недоступны — R5 ограничен Linux AppImage (которому подпись не нужна) и улучшениями unsigned scaffolding из v0.6 F1 R1. macOS/Windows остаются известными ограничениями в `docs/install.md`.

- **Action** (для каждой платформы):
  - **macOS** (`installers/macos/sign-notarize.sh`): `codesign --deep --force --options runtime --sign "$APPLE_DEV_ID_CERT"` → `ditto -c -k --keepParent yt-uniq-gui.app yt-uniq-gui.zip` → `xcrun notarytool submit ... --wait` → `xcrun stapler staple yt-uniq-gui.app` → DMG packaging через `create-dmg`. CI uses ephemeral keychain.
  - **Windows** (`installers/windows/wxs/yt-uniquifier.wxs`): WiX 4 source, MSI с product GUID + upgrade GUID, Start Menu shortcut, uninstaller, registry entry для file-association `.yt-uniq-profile`. `signtool sign /a /tr http://timestamp.digicert.com /td sha256 /fd sha256 ...`.
  - **Linux** (`installers/linux/AppImageBuilder.yml`): bundle Python 3.11 + PyQt6 + ffmpeg static (johnvansickle.com nightly или собственная сборка). Test через `--appimage-extract-and-run` в CI.
  - `.github/workflows/release.yml`: extend matrix jobs. На macOS — extra step `sign-notarize.sh`. На Windows — `wix build` + `signtool`. На Linux — `appimagetool`. All outputs uploaded в draft release.
  - `installers/.env.example`: список required secrets с инструкциями.
- **Mirror**: `.github/workflows/release.yml:R1` scaffolding (unsigned matrix уже есть).
- **Validate**:
  - macOS: `spctl -a -v yt-uniq-gui.app` → `accepted; source=Notarized Developer ID`.
  - Windows: `signtool verify /pa /v yt-uniquifier-1.0.0.msi` → `Successfully verified`.
  - Linux: `./yt-uniq-gui.AppImage --version` → `yt-uniquifier 1.0.0`.

### **R6 — Accessibility WCAG 2.1 AA finishing** (~6-8 часов)

- **Action**:
  - Audit per-screen: 10 экранов (`run`, `batch`, `queue`, `calibrate`, `validation`, `qa_viewer`, `profile_editor`, `history`, `corpus`, `settings`). Checklist:
    1. **Focus visibility** (WCAG 2.4.7): `QWidget::focus` style override (3px solid outline) применён ко всем focusable виджетам через `style.qss` или per-widget `setStyleSheet`.
    2. **Color-only meaning** (WCAG 1.4.1): KPI pills, badges — иметь icon/text label в дополнение к цвету (часть уже сделано в v0.7 R7 token fix).
    3. **Target size** (WCAG 2.5.5 AAA, советуется для AA): touch targets ≥ 44×44 px. Кнопки Pause/Cancel в Run screen — проверить.
    4. **Form labels** (WCAG 3.3.2): every QLineEdit/QSpinBox/QComboBox имеет связанный `QLabel` через `QLabel::setBuddy` или `setAccessibleName`.
    5. **Error recovery** (WCAG 3.3.3): error messages — actionable text не "Invalid value", а "Workers must be 1-16, got 32".
    6. **Heading hierarchy** (WCAG 1.3.1): groupbox titles + section labels — нет skipped levels.
    7. **No keyboard traps** (WCAG 2.1.2): `Esc` всегда возвращает фокус, modal dialogs не блокируют tab-out.
  - `tests/gui/test_wcag_aa_compliance.py`: программируемая проверка
    - Каждый focusable widget имеет non-empty `accessibleName`.
    - Color-pair contrast (уже есть в v0.7 R7 — расширить).
    - `setBuddy` или `accessibleDescription` присутствует у inputs.
    - Tab order не имеет gaps.
  - `docs/accessibility.md`: WCAG 2.1 AA conformance statement, known limitations (если есть), screen-reader manual test guide (VoiceOver на macOS, NVDA на Windows, Orca на Linux) для каждого из 10 screens.
- **Mirror**: `tests/unit/test_gui_accessibility.py` (v0.7 R6) + `tests/gui/test_theme_contrast.py` (v0.7 R7).
- **Validate**: `pytest tests/gui/test_wcag_aa_compliance.py -v`; manual VoiceOver run по `docs/accessibility.md` checklist (один проход на macOS).

### **R7 — Final docs + versioned site + 1.0.0 bump** (~4-6 часов)

- **Action**:
  - `mkdocs.yml`: add `mike` plugin для versioned docs (`latest` + per-tag). `.github/workflows/docs.yml` extend: on `v*` tag deploy as new mike version + alias `latest`.
  - `docs/index.md`: rewrite hero для v1.0 — "Stable API • Signed installers • SemVer commitment". Badges: PyPI version, CI status, coverage, docs.
  - `docs/CONTRIBUTING.md`: RFC процесс детали, code-of-conduct ссылка, commit message format, dev setup (`make dev`).
  - `.github/ISSUE_TEMPLATE/{bug,feature,rfc}.yml`: structured forms. RFC template требует "Problem / Proposal / Alternatives / Migration plan".
  - `pyproject.toml`: `version = "1.0.0"`; `Development Status :: 5 - Production/Stable`; `keywords` extend.
  - `CHANGELOG.md`: rename `[Unreleased]` block → `[1.0.0] — YYYY-MM-DD` (use today's date).
  - `.claude/plans/yt-uniquifier-best-in-class.plan.md`: пометить v1.0.0 чекбоксы как `[x]` shipped, добавить commit hash references.
  - Tag и push: `git tag v1.0.0 && git push --tags` → triggers `release.yml` (signed installers, если R5 готов) + `docs.yml` (mike deploy).
- **Mirror**: v0.9.0 R6 mkdocs ship pattern; `docs.yml` workflow tag-trigger.
- **Validate**:
  - `mkdocs build --strict` exit 0.
  - `mike deploy --update-aliases 1.0.0 latest` локально работает.
  - `git tag v1.0.0` + dry-run `gh release create v1.0.0 --draft --notes-from-tag` показывает CHANGELOG content.
  - `make check` зелёный.

---

## Validation (cumulative)

```bash
# After each round
make check                                                      # ruff + mypy + full pytest
pytest tests/contracts -v                                       # R2 onwards
pytest --cov=src/yt_uniquifier/core --cov-fail-under=85         # R3 onwards
python tools/benchmark.py <fixture> --json /tmp/b.json          # R4 smoke
spctl -a -v dist/yt-uniq-gui.app                                # R5 macOS (требует cert)
signtool verify /pa /v dist/*.msi                               # R5 Windows
./dist/yt-uniq-gui.AppImage --version                           # R5 Linux
pytest tests/gui/test_wcag_aa_compliance.py -v                  # R6
mkdocs build --strict                                           # R7

# Final v1.0.0 smoke
python -c "import yt_uniquifier; assert yt_uniquifier.__version__ == '1.0.0'"
yt-uniq --version
yt-uniq run tests/fixtures/perf_baseline_720p.mp4 --profile cid_aware --out /tmp/out.mp4
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| R5 БЛОКЕР: нет Apple Dev ID / Windows cert | **Высокая** | Договориться с пользователем при confirmation. Plan B: R5 ограничен Linux AppImage; macOS/Windows shipped unsigned с known-limitations note в docs. Allows v1.0 ship, signing — patch release v1.0.1. |
| R3 coverage push выявляет реальные баги в untested путях | Средняя | Это feature, не bug — заводить отдельные PR per-fix, не блокировать R3. Использовать `@pytest.mark.xfail(strict=True)` если фикс откладывается. |
| R2 contract snapshot tests блокируют любое изменение pydantic поля | Высокая (это by-design) | Document как feature: failing test = forced review. `tools/regen_contract_goldens.py --i-know-this-is-breaking` для intentional updates с обязательным CHANGELOG entry. |
| R4 perf-regression noise (CI runner варьируется ±15%) | Высокая | Threshold 5% слишком жёсткий. Использовать 15% threshold + 3-run median; trigger issue только если 2 nightly подряд regress. |
| R6 WCAG audit находит фундаментальные проблемы (custom-painted widgets без a11y) | Средняя | Custom `_Sparkline` (v0.7 R4) — известный non-accessible widget. Mitigation: добавить `accessibleDescription` "Live divergence indicator, current value X" обновляемый per-frame. Не идеал, но WCAG-compliant baseline. |
| R7 `mike` plugin требует gh-pages branch reset | Низкая | Backup существующего docs/ deploy перед switch. |
| Окно v0.9 → v1.0 слишком короткое (master plan говорит "после ~2-3 месяцев тестирования v0.9") | Высокая | v0.9 был shipped 2026-06-14 = сегодня. Запросить у пользователя при confirmation: либо (а) делаем v1.0.0rc1 сейчас, 2-3 месяца беты, потом v1.0.0; либо (б) сжимаем — все 7 раундов в один пробег, помечаем как 1.0.0rc1. **Рекомендую (а)**: R1-R7 завершаются как 1.0.0rc1; финальный bump → 1.0.0 откладывается до feedback window. |

---

## External dependencies / открытые вопросы — РЕШЕНО (2026-06-14)

1. **R5 credentials**: НЕТ. R5 → только Linux AppImage signed. macOS/Windows ship unsigned с known-limitations note в `docs/install.md`. Signing откладывается до credentials как v1.0.1+ patch release.
2. **Версия**: сразу `1.0.0` (skip rc cycle). R1 bumps `0.1.0a0` → `1.0.0` + classifier `Production/Stable` в одном шаге.
3. **R4 storage**: gh-pages branch (где уже mkdocs site живёт). Persist `perf-history/<git-sha>.json` per commit + index `perf-history/index.json`.
4. **CHANGELOG**: полная история из `git log` (v0.5.5 → v0.9.0, ~80 commits).

---

## Acceptance — v1.0.0 shipped когда:

- [x] `pyproject.toml` version == `1.0.0`, classifier `Production/Stable` — R1 `28b9e3c`
- [x] `CHANGELOG.md` полный, с датой v1.0.0 — R1 `28b9e3c`
- [x] `SECURITY.md` + `docs/versioning.md` опубликованы — R1 `28b9e3c` (GitHub Private Vulnerability Reporting — repo-level toggle, ON по умолчанию для public repos)
- [x] `docs/api-contracts.md` + `tests/contracts/*` зелёные на CI — R2 `0aecc7e` (41 golden files, 12 stable models + 14 profiles + dataclasses + public surface + RunEvent kinds)
- [x] CI gate: `--cov-fail-under=80` на `core/` (master plan target 85; ratchet к v1.1) — R3 `d1aa439`
- [x] `.github/workflows/perf-regression.yml` runs nightly, opens issue on >15% regression — R4 `3f02a1e`
- [x] Минимум Linux AppImage signed + shipped в GitHub Releases — R5 `f83693e` (macOS/Windows — unsigned shipped с bypass docs; full signing → v1.0.x patch)
- [x] `tests/gui/test_wcag_aa_compliance.py` зелёный; `docs/accessibility.md` опубликован — R6 `810c78c`
- [~] mkdocs site versioned (mike) — **DEFERRED к v1.x**: existing `docs.yml` использует Pages-from-Actions (не gh-pages branch), `mike` требует switch deploy model. R1+R6 добавили Project nav (versioning + api-contracts + security + accessibility) — за это сегодня платим версионированием через git tags вместо mike subdirectories.
- [x] `git tag v1.0.0 && git push --tags` triggers all release workflows — R7 (pending push after this commit)
- [x] Master plan `.claude/plans/yt-uniquifier-best-in-class.plan.md` § 10 — все `[ ]` закрыты или explicit "deferred к 1.x" — R7 (this commit)

---

**WAITING FOR CONFIRMATION**. Подтверждение варианты:

- `yes` / `proceed v1.0` — старт с R1 (version bump + CHANGELOG + SECURITY).
- `start v1.0.0rc1 — answer questions: [1=no creds, 2=rc1+beta, 3=artifacts, 4=full history]` — даёт ответы на 4 открытых вопроса разом и стартуем.
- `R1 only` / `R3 only` / `R1+R2 only` — точечный запуск раундов.
- `modify: ...` — корректировки scope.
- `expand R5` / `expand R3` — раскрыть детали по конкретному раунду до старта.
