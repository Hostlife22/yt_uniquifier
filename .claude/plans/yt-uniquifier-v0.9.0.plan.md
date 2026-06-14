# Plan: yt-uniquifier v0.9.0 — Community + Web

**Source plan**: `.claude/plans/yt-uniquifier-best-in-class.plan.md` § v0.9.0
**Selected milestone**: epic v0.9.0 (4–6 weeks in master plan; sliced here into 6 atomic rounds)
**Complexity**: **Large**
**Prereqs**: v0.8.0 shipped (commit `2f969d5`), main green on 3-OS × 2-Py CI matrix, `[ml]` / `[scene]` extras stable.

---

## Summary

Six v0.9.0 deliverables from the master plan, sequenced as 6 atomic, ship-shaped rounds. Ordering is risk-ascending and surface-additive: smallest blast radius first (marketplace import → Whisper transform → telemetry), then the largest new surface area (Docker + web UI), then UX polish (i18n) and docs.

The release introduces **three new optional extras**: `[web]` (FastAPI + uvicorn, ~25 MB), `[whisper]` (ffmpeg-side filter — no Python deps, just a runtime check), and `[docs]` (mkdocs-material as a dev-only extra). Core install stays slim. No new always-on dependencies. The desktop GUI gains a translation layer but English remains the source-of-truth and the only required locale.

**Non-goals for v0.9**: cloud SaaS, auto-upload to platforms, custom-GUI plugins (§9 of master plan). Stay in the documented deferred set.

---

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Optional extras | `pyproject.toml` `[project.optional-dependencies] ml = [...]` (v0.8 R4) | Add `web`, `whisper`, `docs` next to `gui`, `qa`, `ml`, `scene`. Imports inside try/except, deferred to first call. |
| Lazy network resource | `core/qa/sscd.py::_ensure_model_cached` (v0.8 R4) | Marketplace catalog fetch: pinned URL + SHA-256 expected, cached under `QStandardPaths.CacheLocation / "marketplace"`, explicit `--refresh` flag, never auto-downloads silently in CI. |
| New transform | `core/transforms/video_color.py` + `transforms/base.py::register` | Whisper transform: `WhisperSubtitlesParams` (pydantic, `extra="forbid"`), builder produces a `subtitles=…` filter chain after Whisper sidecar generation; runtime probe of `ffmpeg -filters | grep whisper` mirrors the encoder real-probe pattern in `core/encoder.py`. |
| Opt-in feature with explicit consent | `core/notifications.py` `NotificationConfig` (v0.7 R5) | Telemetry: disabled by default, requires explicit `enabled=true` + first-run banner in GUI; local-only events written to `QStandardPaths.AppDataLocation / "telemetry" / "events.jsonl"`; CLI export subcommand only. No network egress in v0.9. |
| Thin HTTP layer over `core/` | `core/orchestrator.py::run_full(plan, options, on_event, cancel_token)` | Web app is a FastAPI shell that builds a `Plan` + `RunOptions` exactly like CLI/GUI and calls `run_full`. SSE streams `RunEvent` to the browser. No business logic in `web/`. |
| Subprocess QA metric / encoder probe | `core/encoder.py::detect_encoders` | Whisper availability check uses the same `ffmpeg -filters` parse + cache pattern (no real run, just capability detection). |
| GUI groupbox add | `gui/screens/settings.py` notifications groupbox (v0.7 R5) | Settings gains a "Community profiles" groupbox + "Language" combo + "Telemetry" toggle. Each is an additive QGroupBox; absent extras show a one-line install hint. |
| Tested CLI subcommand | `cli/cmd_corpus.py` (v0.8 R2) `migrate` / `list` / `search` | `yt-uniq profile install <url|name>`, `yt-uniq profile list-community`, `yt-uniq telemetry export <out.jsonl>`, `yt-uniq web` — all wired via typer subcommand app. |

---

## Files to Change (high-level — round detail below)

| Area | Files | Action |
|---|---|---|
| Packaging | `pyproject.toml` | UPDATE — add `[web]`, `[whisper]`, `[docs]` extras; declare `yt-uniq-web` script |
| Profile marketplace | `core/profile_marketplace.py` (NEW), `core/profile_loader.py`, `cli/cmd_profile.py` (NEW), `gui/screens/profile_editor.py`, `gui/workers/marketplace_worker.py` (NEW) | CREATE + UPDATE — catalog fetch, SHA-pinned download, GUI "Browse community profiles" dialog |
| Marketplace catalog | `marketplace/catalog.json` (NEW, repo-local seed) | CREATE — seed entries pointing at `yt-uniquifier-profiles/` GitHub repo |
| Whisper transform | `core/transforms/video_subtitles.py` (NEW), `core/transforms/__init__.py`, `core/transforms/_whisper_probe.py` (NEW), `core/encoder.py` (extend `_filter_available` helper or sibling) | CREATE + UPDATE — params model, builder, runtime probe, opt-in via profile YAML |
| Telemetry | `core/telemetry.py` (NEW), `core/orchestrator.py`, `core/models.py`, `cli/cmd_telemetry.py` (NEW), `gui/screens/settings.py` | CREATE + UPDATE — `TelemetryConfig`, append-only JSONL writer, CLI export, GUI opt-in toggle + first-run banner |
| Docker + web | `Dockerfile` (NEW), `docker-compose.yml` (NEW), `.dockerignore` (NEW), `src/yt_uniquifier/web/__init__.py` (NEW), `web/app.py` (NEW), `web/routes/run.py` (NEW), `web/routes/profile.py` (NEW), `web/routes/qa.py` (NEW), `web/templates/index.html` (NEW), `web/static/app.js` (NEW), `cli/cmd_web.py` (NEW) | CREATE — FastAPI + minimal SPA, multi-stage Dockerfile (ffmpeg base + Python layer), compose for NAS deployment |
| Localization | `src/yt_uniquifier/gui/i18n/__init__.py` (NEW), `gui/i18n/yt_uniquifier_en.ts` (NEW), `gui/i18n/yt_uniquifier_ru.ts` (NEW), compiled `.qm` (build step), `gui/app_pyqt.py`, `gui/screens/settings.py` | CREATE + UPDATE — QTranslator install, `tr()` wrapping for user-visible strings on all 10 screens, language combo in Settings, locale persisted via `QStandardPaths.AppConfigLocation` |
| Docs site | `mkdocs.yml` (NEW), `docs/index.md` (NEW), `docs/getting-started.md` (NEW), `docs/cli-reference.md` (NEW), `docs/gui-walkthrough.md` (NEW), `.github/workflows/docs.yml` (NEW) | CREATE — mkdocs-material site, GH Pages deploy on tag, video walkthrough placeholders |
| Master plan | `.claude/plans/yt-uniquifier-best-in-class.plan.md` | UPDATE — flip v0.9.0 row to in-progress on R1 start; checkbox to `[x]` per round on landing |
| Tests | `tests/unit/test_profile_marketplace.py`, `tests/unit/test_whisper_subtitles_offline.py`, `tests/integration/test_whisper_real_ffmpeg.py`, `tests/unit/test_telemetry_writer.py`, `tests/unit/test_telemetry_redaction.py`, `tests/unit/test_web_routes.py`, `tests/integration/test_web_run_smoke.py`, `tests/unit/test_i18n_strings_coverage.py`, `tests/integration/test_docker_image_smoke.sh` | CREATE |

---

## Round-by-round breakdown

### R1 — Profile marketplace (F9)

**Goal**: Users can browse and install community-contributed YAML profiles from a curated GitHub repo without leaving the GUI/CLI. No exec on import — profiles are pure declarative YAML and validated by the existing `extra=forbid` schema.

- `core/profile_marketplace.py`:
  - `CatalogEntry(BaseModel)` — `id`, `name`, `description`, `url` (raw GitHub URL), `sha256`, `author`, `tags: list[str]`, `version`, `min_yt_uniquifier_version`.
  - `Catalog(BaseModel)` — top-level `version` + `entries: list[CatalogEntry]`. Validated `extra="forbid"`.
  - `fetch_catalog(refresh: bool = False) -> Catalog` — pull from pinned `CATALOG_URL` (raw GitHub URL of `yt-uniquifier-profiles/main/catalog.json`), validate, cache under `QStandardPaths.CacheLocation / "marketplace" / "catalog.json"`. SHA-256 verified.
  - `install(entry: CatalogEntry, dest: Path) -> Path` — download, verify SHA, run through `profile_loader.load_profile` to confirm schema compliance, atomic write to `dest`. Never runs untrusted code.
- `core/profile_loader.py` — add `load_from_marketplace(name: str) -> Profile` thin convenience that delegates to the above and returns a validated `Profile`.
- `cli/cmd_profile.py` (new typer subapp wired into `cli/app.py`):
  - `yt-uniq profile list-community [--refresh]` — table of catalog entries (id, name, tags, version).
  - `yt-uniq profile install <id> [--dest PATH]` — installs to `$XDG_CONFIG_HOME/yt_uniquifier/profiles/<id>.yaml`.
  - `yt-uniq profile show <id>` — inspect catalog entry before install.
- `gui/screens/profile_editor.py`:
  - "Browse community…" button opens a modal with a `QTableView` of catalog entries (sortable by tag/popularity).
  - "Install" button delegates to `MarketplaceWorker` (background); on success, the just-installed profile is selected in the editor.
- `gui/workers/marketplace_worker.py` — wraps `fetch_catalog` and `install`, streams progress, respects `request_cancel()`.
- `marketplace/catalog.json` — seed catalog (3–5 entries: `cid_aware`, `tiktok_high_motion`, `podcast_loudnorm_only`, `screencast_no_color`). This file is shipped *in the repo* as the v0.9 bootstrap; the long-term home is the `yt-uniquifier-profiles/` repo (created out-of-band).
- Tests `test_profile_marketplace.py`:
  - Mock `urllib.request.urlopen` to return a fake catalog; assert validation, SHA mismatch raises, schema-invalid profile rejected before write.
  - Round-trip: install a fixture YAML, then `Profile` loads identically to direct load.
  - `extra="forbid"` blocks a catalog entry with unknown fields.
- **Mirror**: `core/qa/sscd.py::_ensure_model_cached` (pinned URL + SHA), `core/profile_loader.py::load_profile` (pydantic strict validation).
- **Validate**: `pytest tests/unit/test_profile_marketplace.py -v && make lint && make typecheck`.
- **Commit**: `feat(core,cli,gui): v0.9.0 R1 — community profile marketplace (F9)`.

### R2 — Whisper subtitle transform (F14)

**Goal**: Optional `video.whisper_subtitles` transform that runs a Whisper-based subtitle pass over the source (via ffmpeg 8.0 native filter when present, or a sidecar `whisper-cpp` invocation when not) and burns the result into the video. Strictly opt-in — profile must explicitly enable it.

- `core/transforms/_whisper_probe.py` — `whisper_capability() -> Literal["ffmpeg_native","whispercpp","none"]` with `functools.lru_cache`. Parses `ffmpeg -filters` for the `whisper` filter (ffmpeg 8.0); falls back to checking `whisper-cpp` on PATH; otherwise `"none"`.
- `core/transforms/video_subtitles.py`:
  - `WhisperSubtitlesParams(BaseModel, extra="forbid")` — `model: Literal["tiny","base","small","medium"]="base"`, `language: str | None = None` (auto-detect), `burn_in: bool = True`, `font_size: int = 24`, `position: Literal["bottom","top"]="bottom"`, `max_chars_per_line: int = 42`.
  - `build(...)` — if `capability == "ffmpeg_native"`: produce `whisper=model=<m>:language=<l>,subtitles=…` in the filter chain. If `whispercpp`: generate an `.srt` sidecar via subprocess (cached by source `(size, mtime_ns)` — same trick as v0.6 B1), then append a `subtitles=<srt>` filter to the chain. If `none`: raise `PreflightError` with the install hint, surfaced in preflight rather than mid-run.
- `core/transforms/__init__.py` — register, wrapped in v0.8 R1 try/except (won't kill tool if whisper-cpp missing).
- `core/preflight.py` — new `WhisperUnavailableFinding(severity="blocker")` when a profile requests the transform but capability is `"none"`. Includes resolution hint.
- `pyproject.toml` — `[whisper]` extra is documentation-only (no Python deps; the binary is provided by the user). Documented in `docs/install.md`.
- Tests:
  - `test_whisper_subtitles_offline.py` — snapshot the `-filter_complex` fragment for the `ffmpeg_native` path (mocked capability); assert sidecar-mode constructs correct `subtitles=` filter with shell-escaped path.
  - `test_whisper_real_ffmpeg.py` — `@pytest.mark.integration` + skip-if-not-`ffmpeg_native`. Runs end-to-end on `tiny_clip` with audio overlay.
- **Mirror**: `core/transforms/audio_loudnorm.py` (cached subprocess artifact, two-phase), `core/encoder.py::detect_encoders` (capability cache).
- **Validate**: `pytest tests/unit/test_whisper_subtitles_offline.py -v && make lint && make typecheck`.
- **Commit**: `feat(core): v0.9.0 R2 — Whisper subtitle transform (F14)`.

### R3 — Telemetry opt-in

**Goal**: Anonymous, transparent, local-only events that the user can later export and share if they want to. Disabled by default. Zero network egress in v0.9 — only `JSONL` to disk.

- `core/telemetry.py`:
  - `TelemetryConfig(BaseModel, extra="forbid")` — `enabled: bool = False`, `redact_paths: bool = True`, `events_dir: Path | None = None` (defaults to `QStandardPaths.AppDataLocation / "yt_uniquifier" / "telemetry"`).
  - `record(event: dict[str, Any], config: TelemetryConfig) -> None` — no-op when disabled; otherwise append-only JSONL with `ts`, `event_id` (UUID4), `version` (yt-uniquifier `__version__`), and the event body. Path redaction: replaces absolute paths with `<HOME>`-prefixed forms; never logs `--output`, `--input` raw paths when `redact_paths=True`.
  - `iter_events(events_dir: Path) -> Iterator[dict]` — for the export subcommand.
- `core/orchestrator.py` — at the `RunSummary` boundary, if telemetry enabled, emit one event with: profile id, encoder kind+vendor, total segments, total wall-clock, total retries, OS, Python version. **No** paths, no audio fingerprints, no source duration.
- `cli/cmd_telemetry.py`:
  - `yt-uniq telemetry status` — prints current config + event count.
  - `yt-uniq telemetry export <out.jsonl>` — copies events out.
  - `yt-uniq telemetry purge --yes` — wipes events dir.
- `gui/screens/settings.py` — Telemetry groupbox: opt-in toggle, "View events" → opens `events_dir` in OS file explorer, "Export…" button (shells to CLI). First-run banner asks for consent ONCE; never re-prompts unless user clicked "Decide later".
- `gui/app_pyqt.py` — first-run consent dialog when `~/.config/yt_uniquifier/telemetry-consent` absent. Default decision is OFF; explicit click required to enable.
- Tests:
  - `test_telemetry_writer.py` — disabled config = no file created; enabled config = JSONL append, schema validated; concurrent writes from two threads = no torn JSON lines (mirror `CheckpointStore` lock pattern).
  - `test_telemetry_redaction.py` — `redact_paths=True` strips `$HOME` prefix, preserves event semantics; `redact_paths=False` keeps raw.
- **Mirror**: `core/notifications.py::NotificationConfig` (opt-in pattern + GUI groupbox), `core/checkpoint.py::CheckpointStore._flush` (atomic append).
- **Validate**: `pytest tests/unit/test_telemetry_writer.py tests/unit/test_telemetry_redaction.py -v && make lint && make typecheck`.
- **Commit**: `feat(core,cli,gui): v0.9.0 R3 — opt-in local telemetry`.

### R4 — Docker headless + web UI (F13)

**Goal**: A NAS-friendly headless deployment. Single container, ffmpeg baked in. Minimal web UI that builds a `Plan` + `RunOptions` and streams `RunEvent`s via SSE — no business logic on the web side.

- `Dockerfile` — multi-stage:
  - Stage 1 (`builder`): `python:3.12-slim` + `pip wheel . [web]`.
  - Stage 2 (`runtime`): `jrottenberg/ffmpeg:7-alpine` or equivalent + Python slim layer; copies wheels; non-root user; `EXPOSE 8080`; `ENTRYPOINT ["yt-uniq-web", "--host", "0.0.0.0", "--port", "8080"]`.
  - Health check: `curl -fsS http://localhost:8080/healthz || exit 1`.
- `docker-compose.yml` — single service, volume mounts for `/data/input`, `/data/output`, `/data/work`, `/data/profiles`. Environment vars for default profile.
- `.dockerignore` — exclude `.venv`, `__pycache__`, `tests/`, `dist/`, `.git/`.
- `src/yt_uniquifier/web/`:
  - `app.py` — FastAPI app factory; routers wired; CORS off by default (LAN use); basic auth via env vars (`YT_UNIQ_WEB_USER` / `YT_UNIQ_WEB_PASS`), 401 on absent creds **only** if the env vars are set (frictionless local-LAN default).
  - `routes/run.py` — `POST /api/run` (body: `{input_path, profile_id, options}`) → spawns `run_full` in `asyncio.to_thread`; returns `run_id`. `GET /api/run/{id}/events` SSE stream of `RunEvent`. `POST /api/run/{id}/cancel`.
  - `routes/profile.py` — list/show/install (delegates to R1 marketplace).
  - `routes/qa.py` — serves `<out>.qa.html` and `<out>.qa.json`.
  - `templates/index.html` + `static/app.js` — minimal SPA (vanilla JS, no framework): input picker, profile combo, "Run" button, live event log, progress bar.
- `cli/cmd_web.py` — `yt-uniq web [--host 127.0.0.1] [--port 8080] [--workers 1]` — uvicorn launcher.
- `pyproject.toml` — `[web]` extras: `fastapi~=0.115`, `uvicorn[standard]~=0.30`, `python-multipart~=0.0.9`; `yt-uniq-web` script.
- Tests:
  - `test_web_routes.py` — FastAPI `TestClient`; assert routes return correct shape; cancel endpoint cancels; basic auth gating works when env vars set.
  - `test_web_run_smoke.py` — `@pytest.mark.integration`; spin up app, post a real `tiny_clip` run, drain SSE, assert `completed` event arrives with summary.
  - `test_docker_image_smoke.sh` — bash; builds image, runs container with `--rm`, hits `/healthz`, asserts 200. Wired into a CI job conditional on `runner.os == 'Linux'` with Docker available.
- **Mirror**: `core/orchestrator.py::run_full` signature, `cli/cmd_run.py` plan-building (web reuses the exact same builder helpers — extracted to `cli/_plan_builder.py` if duplication appears).
- **Validate**: `pytest tests/unit/test_web_routes.py -v && pytest -m integration tests/integration/test_web_run_smoke.py -v && bash tests/integration/test_docker_image_smoke.sh`.
- **Commit**: `feat(web,docker): v0.9.0 R4 — headless deployment + FastAPI web UI (F13)`.

### R5 — Localization (en + ru) via QTranslator

**Goal**: GUI strings wrapped in `self.tr(...)` / `QObject.tr(...)`. Two locales shipped (`en_US` baseline, `ru_RU` translation). CLI stays English-only (it's developer-facing). Locale switch hot-reloads without restart.

- `gui/i18n/__init__.py` — `install_translator(app: QApplication, locale: str) -> None`; on miss, falls back to `en_US`.
- `gui/i18n/yt_uniquifier_en.ts` — generated via `pylupdate6` (Qt's translation extractor); treat as source-of-truth for the catalog.
- `gui/i18n/yt_uniquifier_ru.ts` — hand-translated for the high-traffic strings (Run, Settings, Batch, common buttons). Lower-traffic screens may stay English in v0.9 (acceptable degradation — `QTranslator` returns source on miss). Document the coverage in `docs/i18n.md`.
- Compiled `.qm` files generated at build time by a `Makefile` target (`make i18n`) and bundled into the wheel via `[tool.setuptools.package-data]`.
- `gui/app_pyqt.py` — read persisted locale from `QStandardPaths.AppConfigLocation` on boot; install translator before any widget is created.
- `gui/screens/settings.py` — Language combo (English / Русский / System default). Change → persist + `QApplication.installTranslator` swap + emit `state.locale_changed` → screens that have cached strings re-render (same pattern as v0.7 R4 theme change).
- Wrap user-visible strings on all 10 screens. Mechanical pass; no logic changes. Numbers / units stay locale-neutral (no QLocale numeric formatting in v0.9 — defer to v1.0).
- `tests/unit/test_i18n_strings_coverage.py` — walks `gui/screens/` AST and asserts every `QLabel(...)` / `QPushButton(...)` literal string is wrapped in `tr()` (regression guard against future drift). Tolerates explicit `# i18n: skip` comments.
- **Mirror**: v0.7 R4 theme-change subscriber pattern in `gui/widgets/kpi_pills.py`.
- **Validate**: `make i18n && pytest tests/unit/test_i18n_strings_coverage.py -v && make test-gui`.
- **Commit**: `feat(gui): v0.9.0 R5 — localization (en + ru) via QTranslator`.

### R6 — Docs site + smoke + master-plan flips

**Goal**: Replace ad-hoc `docs/*.md` browsing with a published mkdocs-material site. CI publishes to GitHub Pages on tag push.

- `mkdocs.yml` — material theme; nav covers: Home, Getting Started, CLI Reference, GUI Walkthrough, Architecture (links to `docs/architecture.md`), Profiles, Plugins, SSCD, Corpus, Calibrate, QA Report, Distributed, Troubleshooting.
- `docs/index.md` (NEW) — landing page; 90-second elevator pitch + install one-liner + screenshot.
- `docs/getting-started.md` (NEW) — CLI + GUI quickstart, 5 minutes to first re-encode.
- `docs/cli-reference.md` (NEW) — autogenerated table of subcommands + flags (script that calls `yt-uniq --help` and stitches output; idempotent regen via `make docs`).
- `docs/gui-walkthrough.md` (NEW) — screenshot tour of the 10 screens (uses existing `docs/screenshots/`); placeholder slots for the 6 short video walkthroughs (recorded out-of-band).
- `docs/marketplace.md` (NEW) — how the catalog works, how to contribute a profile (PR template), trust model.
- `docs/web.md` (NEW) — Docker deployment, environment variables, basic auth, reverse-proxy notes.
- `docs/telemetry.md` (NEW) — exactly what's collected, where it's stored, how to export/purge, why it's off by default.
- `docs/i18n.md` (NEW) — adding a locale via `pylupdate6` + Linguist; current coverage matrix.
- `pyproject.toml` `[docs]` extra — `mkdocs-material~=9.5`, `pymdown-extensions~=10.9`.
- `.github/workflows/docs.yml` — on tag push (`v*.*.*`), `mkdocs gh-deploy --force` to `gh-pages` branch.
- `tests/integration/test_v090_smoke.py` — single end-to-end run combining: marketplace install (mocked catalog) + Whisper transform (skipped if capability absent) + web SSE smoke. Polite skips when extras absent.
- `.claude/plans/yt-uniquifier-best-in-class.plan.md` v0.9.0 row — flip each entry to `[x]` with the round commit SHAs as R1–R5 land; this commit closes the row.
- **Validate**: `mkdocs build --strict && pytest tests/integration/test_v090_smoke.py -v && make check`.
- **Commit**: `docs+test: v0.9.0 R6 — mkdocs site, smoke, master-plan flips`.

---

## Validation (after each round)

```bash
make lint                    # ruff
make typecheck               # mypy --strict
make test-unit               # ~10s baseline
make check                   # full pipeline (gate)
pytest tests/unit/test_<round-specific>.py -v
```

Per-round integration smoke (when applicable):

```bash
pytest tests/unit/test_profile_marketplace.py -v           # R1
pytest tests/integration/test_whisper_real_ffmpeg.py -v    # R2 (only when ffmpeg whisper filter present)
pytest tests/unit/test_telemetry_writer.py -v              # R3
pytest -m integration tests/integration/test_web_run_smoke.py -v   # R4
bash tests/integration/test_docker_image_smoke.sh          # R4 (Linux + Docker)
pytest tests/unit/test_i18n_strings_coverage.py -v         # R5
mkdocs build --strict                                       # R6
pytest tests/integration/test_v090_smoke.py -v             # R6
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Marketplace catalog hosts go 404 or are taken over → users install hostile YAML | Medium | SHA-256 pinning per entry; profile YAML can't execute code (declarative, `extra=forbid`); never download without checksum match; CLI prints catalog source URL before install |
| Whisper ffmpeg native filter not yet in the user's ffmpeg build → preflight fails on profiles that request it | High | Preflight catches it as a blocker with install instructions; `whisper-cpp` sidecar fallback documented; profile catalog entries that need Whisper are tagged `requires: whisper` |
| Web UI exposes filesystem to LAN attackers | High | Default bind `127.0.0.1`; explicit `--host 0.0.0.0` required; basic auth gating via env vars; document reverse-proxy + HTTPS as the recommended deployment (`docs/web.md`); never serve outside the configured `/data/*` mounts |
| Docker image size balloons with ffmpeg + Python + torch | High | `[ml]` extra deliberately NOT installed in the published image; users add it via `pip install yt-uniquifier[ml]` inside the container if they want SSCD; document the bake-your-own pattern |
| Telemetry collected without consent | High | Off by default; first-run GUI dialog explicit; no network egress in v0.9 (local JSONL only); explicit "Decide later" path; `yt-uniq telemetry purge` instant deletion |
| QTranslator coverage drift — new strings added without `tr()` | Medium | `test_i18n_strings_coverage.py` AST scan in CI; `# i18n: skip` opt-out comments for legitimate non-translatable strings (URLs, log keys) |
| Russian translations lag English releases | High | Documented expectation: untranslated strings fall through to English via `QTranslator`. Acceptable. Catalog of high-traffic strings is the contract, not 100% coverage |
| mkdocs build fails on docs/`.md` files that aren't in `nav:` | Medium | `mkdocs.yml` opts into `strict: true` (warnings become errors); CI rebuild on every PR; missing-from-nav files are explicit errors |
| FastAPI dependency creep | Low | Pinned upper bounds; web app stays a thin shell — any non-trivial logic that appears in `web/` gets promoted to `core/` (mirrors the GUI-worker rule from CLAUDE.md) |
| Docker image fails on Apple Silicon (linux/arm64) NAS | Medium | Multi-arch build via `docker buildx`; CI matrix includes arm64 build (not run, just built) |

---

## Out of scope (deferred)

- Telemetry **network egress** — local-only in v0.9; uploading aggregate stats is a v1.0+ conversation with explicit second-layer consent.
- Web UI for QA viewer / batch / calibrate — v0.9 web ships **only** the Run screen surface (and profile install / QA serve). Full screen parity = v1.0.
- iOS/Android — explicit out-of-scope per master plan §9.
- Cloud SaaS — out-of-scope per master plan §9.
- Auto-upload to YouTube/TikTok — out-of-scope per master plan §9 (TOS risk).
- Custom-GUI plugins — transforms plug in (v0.8 R1), but custom GUI screens stay out (per master plan §9).
- Live video walkthroughs — placeholder slots in docs site; actual recordings produced out-of-band, not blocking R6.
- Right-to-left locales (ar, he) — defer to v1.0; QSS may need mirroring work.
- mkdocs versioned docs (mike) — v0.9 publishes "latest" only.

---

## Acceptance

- [ ] R1–R6 all merged, each as one atomic commit, `make check` green at every commit boundary
- [ ] `yt-uniq profile list-community` and `yt-uniq profile install <id>` work end-to-end against the seed catalog
- [ ] GUI "Browse community profiles" dialog lists ≥ 3 entries and installs one successfully into the per-user profiles dir
- [ ] `video.whisper_subtitles` transform produces a watchable burned-in subtitle on `tiny_clip` when capability is `ffmpeg_native` (integration test); preflight blocks cleanly when capability is `"none"`
- [ ] `yt-uniq telemetry status` reports the correct enabled state; a run with telemetry enabled produces exactly one JSONL event with redacted paths; export round-trips the events
- [ ] Docker image builds on linux/amd64 and linux/arm64; `docker run --rm yt-uniquifier:v0.9 yt-uniq --version` prints the version
- [ ] `yt-uniq web` serves on `127.0.0.1:8080`; POSTing a run and draining SSE produces a `completed` event with a valid `RunSummary`
- [ ] GUI launched with `LANG=ru_RU.UTF-8` shows Russian on Run, Settings, Batch screens
- [ ] `mkdocs build --strict` exits 0; `.github/workflows/docs.yml` deploys on a tag push (verified by manual tag of a release candidate)
- [ ] Master plan `.claude/plans/yt-uniquifier-best-in-class.plan.md` v0.9.0 row marked `[x]` with commit refs

---

**WAITING FOR CONFIRMATION**. Options:

- **"yes"** / **"proceed R1"** — start with R1 (profile marketplace, smallest blast radius)
- **"order: R<n>,R<m>,…"** — re-sequence rounds (e.g. R4 docker first if you need it for early NAS testing)
- **"split R<n>"** — break a round into smaller commits (R4 web is the biggest; happy to split FastAPI scaffold from SSE wiring from SPA HTML, or split R5 i18n by screen)
- **"drop R<n>"** — skip a round entirely from this release (R5 i18n is the most defer-able; R6 docs site is the most independently deferrable)
- **"expand R<n>"** — write a detailed sub-plan for one round before starting
