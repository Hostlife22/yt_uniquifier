# Changelog

<!-- AUTO-GENERATED: sections below v0.3.3 are regenerated from git tags;
     manual prose may be appended within each version block. -->

All notable changes per tagged release. Format: keep-a-changelog style;
versioning follows the git tags `v0.1.0`, `v0.2.0`, `v0.3.x`, `v0.4.x`,
`v0.5.x`.

The `[Unreleased]` section, if present, summarises post-tip changes since
the last tag.

## [Unreleased]

## [1.4.0] — 2026-09-02

### Added

- Add a default 10-minute FFmpeg silent-stall watchdog and opt-in wall timeout;
  configure them with `YT_UNIQ_STALL_TIMEOUT_SEC` and
  `YT_UNIQ_WALL_TIMEOUT_SEC` (`0` disables either policy).
- Probe the selected encoder at the job's actual resolution, pixel format, color
  tags, and rate-control mode before segment work begins.
- Preserve audio/subtitle titles and dispositions, report container-imposed
  metadata loss, and validate representable stream metadata in the final output.
- Detect HDR10 mastering display, MaxCLL/MaxFALL, HDR10+, and Dolby Vision side
  data. Reinject static HDR10 metadata through libx265 and reject unsupported
  dynamic HDR preservation before encoding.
- Persist bounded web run status atomically across restarts, with interrupted-run
  recovery, seven-day retention, and configurable record limits.
- Use a per-user web work directory by default instead of a predictable shared
  `/tmp` path.

### Fixed

- Use one encoder argument policy for full-file, segmented, and capability-probe
  paths instead of divergent VideoToolbox/x26x settings.
- Include the platform, device selection, and NVIDIA driver identity in the
  encoder availability cache key, and do not cache transient job-probe failures.
- Terminate FFmpeg and its watcher if a frontend progress callback fails, so a
  paused or silent child process cannot be orphaned.
- Give automatic shared-filesystem workers process-unique heartbeat identities so
  a live sibling on the same host cannot mask an abandoned lease.
- Preserve `target_vmaf` behavior in distributed worker mode instead of silently
  clearing the profile setting.
- Keep the Intel-macOS ML extra compatible by constraining it to NumPy 1.x and an
  OpenCV release that accepts that ABI; real Torch/NumPy conversion is covered by
  the local production matrix.
- Raise production Pillow and optional cryptography lower bounds to releases that
  fix the known 2026 advisories, require patched Click, and likewise raise
  vulnerable dev/docs lower bounds.
- Require Torch 2.10+ on supported ML platforms; document the Intel macOS 2.2.2
  exception as trusted, SHA-256-pinned SSCD inference only.
- Upgrade the optional scene backend to PySceneDetect 0.7.1 so it can coexist with
  the patched Click runtime and no longer installs conflicting OpenCV wheels.
- Make repeated `make build` invocations non-interactive and keep optional
  ML/web/scene stacks out of the desktop bundle even when they are installed in
  the developer environment.

## [1.3.3] — 2026-09-02

### Fixed

- Package the Windows PyInstaller bundle in a native `pwsh` step and make a missing
  release archive fatal. Earlier Bash expansion consumed `$env` from the embedded
  PowerShell command, so v1.3.2 produced a successful draft without a Windows ZIP.

## [1.3.2] — 2026-09-02

### Fixed

- Use the CycloneDX 4.x-compatible `-o`/`--of` CLI flags when generating the release
  SBOM. The application bundles for v1.3.1 built successfully, but its publication
  job stopped at the obsolete long-form output option; v1.3.2 supersedes that tag.

## [1.3.1] — 2026-09-02

Production correctness and recovery hardening after the repository-wide audit.

### Fixed

- Preserve video PTS/frame count by encoding video-only segments with an explicit
  zero-based timeline; separately mux the selected source audio, subtitles, and
  chapters through a container-aware policy.
- Build pitch from the probed input sample rate, normalize final audio to 48 kHz,
  and measure EBU R128 loudness after all preceding audio transforms. Stereo upload
  audio now uses the documented 384 kbps target; mono and surround use layout-aware
  rates.
- Preserve main audio even when a profile has no audio transform. Honour
  `audio_tracks: first`, `all`, and explicit absolute ffprobe stream indices; convert
  text subtitles to `mov_text` for MP4/MOV and reject incompatible image subtitles
  during preflight.
- Preserve chapter titles and selected audio/subtitle language metadata. Reject
  unsupported timeline-rate combinations instead of silently desynchronizing copied
  streams, chapters, or subtitles.
- Correct divergent-window overlap so duration no longer grows by 0.1 seconds per
  boundary. Reject the stereo-only Haas filter for mono/surround inputs.
- Correct SSCD calibration direction, use common fixed random seeds, bracket the
  search, key clip caches by source content, and abort after bounded retry instead
  of interpreting an encode failure as an optimization signal.
- Include source content and complete stream topology in plan identity. Validate and
  hash cached main audio/final output, acquire work locks atomically, and always
  release checkpoint ownership.
- Make parallel segment failures cancel sibling FFmpeg processes even when callers
  omit a cancellation token. Bound web run concurrency, reject duplicate output
  reservations, and make SSE completion signalling non-blocking.
- Restore square-pixel SAR after crop/resize and define `max_strength` as the maximum
  total crop per axis, rather than independently applying the maximum to both sides.
- Use the installed package version in FastAPI metadata and prevent QA correctness
  failures from receiving a green verdict.

### Added

- Mandatory final media-contract validation for video/audio/subtitle/chapter counts,
  timeline duration, and requested HDR preservation.
- Regression tests with real FFmpeg for 44.1 kHz audio, LUFS, text subtitles,
  chapters, mixed AAC/Opus tracks, frame preservation, and 125-second windowed audio.
- `AUDIT.md`, `RISK_REGISTER.md`, `PRODUCTION_PLAN.md`, `BENCHMARKS.md`, and
  `PRODUCTION_CHECKLIST.md` with verified/deferred production scope.

## [1.3.0]

AV1 + plugin sandbox + cross-OS quality. Backwards-compatible. MINOR.
See `.claude/plans/v1.0.1-to-v1.3-roadmap.plan.md` for the full roadmap.

### Added

- **Task 22: AV1 output** — `core/encoder.py` enumerates the AV1
  software encoders `libsvtav1` (default) and `libaom-av1`, plus the
  hardware variants `av1_nvenc`, `av1_qsv`, `av1_amf`, and
  `av1_videotoolbox` alongside the existing `av1_vulkan`. AV1 uses a
  0..63 CRF scale (default 30 ≈ libx264 CRF 18); hardware AV1 reuses
  the existing 0..51 GPU quality mapping so a single `crf_override`
  drives the whole AV1 matrix. New profiles `youtube_av1.yaml`
  (1080p) and `youtube_4k_av1.yaml` (4K). `EncoderVendor` gains
  `svtav1` and `libaom` tags.
- **Task 23: plugin manifest + capability gate + audit-hook sandbox**
  — third-party transform plugins discovered via the
  `yt_uniquifier.transforms` entry-points group must now ship a
  `yt_uniquifier_plugin.toml` manifest declaring `name`, `version`,
  and `capabilities` (`video_transform` and/or `audio_transform`).
  `register()` is gated on the active manifest. Plugin code runs
  inside a `sys.addaudithook` gate that raises `PluginViolation` on
  denylisted operations (filesystem writes, network egress,
  subprocess spawns, dynamic exec). Built-in transforms bypass the
  gate so legitimate IO keeps working. New Typer flags
  `--no-plugins`, `--plugins-allowlist a,b`, `--unsafe-plugins`;
  env-var equivalents take effect before plugin discovery.
- **Task 25: signed marketplace entries** — `CatalogEntry` gains an
  optional 128-hex-char `signature` field (Ed25519 over the body
  SHA). `install(..., require_signature=True)` (or env
  `YT_UNIQ_REQUIRE_SIGNED_PROFILES=1`) hard-rejects unsigned and
  invalid-signature entries. New `[crypto]` extra pulls
  `cryptography>=42,<46` lazily. Bundled key set at
  `src/yt_uniquifier/keys/marketplace.pub` supports multi-key
  rotation. `docs/marketplace.md` gains a signing workflow + key
  rotation policy.
- **Task 27: property + mutation + chaos test infrastructure**
  — `tests/property/` adds Hypothesis-driven property tests for
  `plan_hash` (determinism, codec/encoder sensitivity) and
  `FilterGraph` (every shipped transform combination builds, output
  labels unique, even-dims guard at tail).
  `tests/chaos/test_random_sigkill.py` SIGKILLs the orchestrator N
  times then resumes, asserting VMAF ≥ 99 against a clean baseline.
  New weekly `.github/workflows/mutation.yml` runs mutmut on
  `core/` with a v1.2.0 floor of 40 % kill rate (v1.3.0 target: 70 %).
- **Task 28: PGO cache for ETA prediction** — new `core/pgo.py`
  records each successful run's `(resolution_bucket, codec,
  encoder_kind) → (workers, segment_sec, seconds_per_min)` into
  `~/.cache/yt_uniquifier/pgo.sqlite`. `--dry-run` ETA uses the
  calibrated prediction when available; cache miss falls back to
  the v1.1.0 heuristic. Writers use `BEGIN IMMEDIATE` so concurrent
  `yt-uniq batch` workers serialise cleanly.
- **Tasks 30–35: production guardrails and operations** — persistent-corner
  watermark detection with explicit ownership attestation, DRM preflight,
  per-run JSONL audit records, cosign-verified updater, opt-in OpenTelemetry,
  and Chinese/Spanish/Brazilian-Portuguese GUI locales.

### Fixed

- **Web path confinement** — `/api/run` now normalizes input, profile, and
  output paths before filesystem access, rejects symlink escapes, and confines
  inputs to the current working directory unless `input_root` is explicitly set.
- **Direct GUI screen shutdown** — every screen now cancels and joins both
  direct workers and nested encoder-detection threads when closed, preventing
  Qt from aborting on teardown with a live `QThread`.
- **Windows ffmpeg pipe deadlock** — runner logs and machine-readable progress
  are now drained through one pipe, preventing verbose ffmpeg/filter output
  from filling stderr and freezing segment encoding indefinitely.
- **Windows keyframe cache races** — atomic cache replacement now retries
  short-lived destination locks from concurrent writers or antivirus scanners,
  while retaining the whole-file/no-torn-write guarantee and cleaning up temp
  files after a terminal failure.
- **Watermark false positives** — detection now requires a strong match for
  the same synthetic template/corner across at least 60% of uniformly spaced
  samples. A single ordinary rectangle can no longer block every encode.
- **SSCD production path** — replaced placeholder model hashes with the
  verified official TorchScript SHA-256, removed the advertised but
  unpublished ONNX artifact, verifies cached weights on every load, applies
  upstream ImageNet normalization, and samples the complete timeline.
- **Atomic final mux** — concat writes to a per-run temporary file and uses
  atomic replacement only after a non-empty success, preserving an existing
  good output when ffmpeg fails. Sources with more than three audio tracks no
  longer lose tracks at concat.
- **Short/silent audio and target dimensions** — non-finite EBU R128 pass-1
  measurements now fall back to dynamic loudnorm instead of crashing FFmpeg;
  crop-resize rounds back to the nearest even source canvas so platform
  profiles retain their promised 1080p/4K dimensions.
- **Release gates and contracts** — synchronized additive v1.1–v1.3 model,
  `RunOptions`, encoder-vendor, and AV1-profile snapshots; restored clean Ruff
  and strict-mypy runs; added real 4K AV1 profile coverage.
- **Cross-platform CI and container build** — regenerated the hashed dev lock
  as a universal resolution (including Hypothesis, Ed25519 test support, and
  Windows colorama), and rebuilt the Docker path around a native multi-arch
  Debian FFmpeg plus a complete offline web wheelhouse. Encoder-selector
  detection threads are now cancelled and joined before nested GUI widgets
  are destroyed, preventing intermittent Windows Qt process aborts.

### CI

- **Task 26: cross-OS integration tests** — real-ffmpeg integration
  tests now run on all six matrix cells
  (`{ubuntu, macos, windows} × {3.11, 3.12}`) instead of
  `ubuntu+3.12` only. Adds a 15 min job-level timeout per cell as
  the worst-case wall-clock guard.

## [v1.1.0] — 2026-06-14

Distribution trust + observability + UX. Backwards-compatible. MINOR.
See `.claude/plans/v1.0.1-to-v1.3-roadmap.plan.md` for the full roadmap.

### Added

- **Tasks 8–9: zero-cost code-signing & bypass docs**
  - `installers/macos/codesign-adhoc.sh` ad-hoc-signs the PyInstaller
    `.app` bundle (`codesign --deep --force --sign -`) so the bundle
    stabilises across macOS updates. `release.yml` invokes it on the
    macOS build leg.
  - Windows stays unsigned (zero registrations); `docs/install.md`
    walks users through SmartScreen Path A (More info → Run anyway)
    and Path B (`Unblock-File`).
- **Task 10: CycloneDX SBOM** — `release.yml` runs `cyclonedx-py
  environment` and ships `sbom.cdx.json` next to the archives.
- **Task 11: cosign release signing** — every artifact (AppImage,
  `.app.zip`, `.exe.zip`, SBOM, SHA256SUMS) gets a `.cosign.bundle`
  via GitHub OIDC. `docs/install.md` ships the verify-blob recipe.
- **Task 12: multi-arch Docker** — new `.github/workflows/docker.yml`
  builds linux/amd64 + linux/arm64 images on every `v*.*.*` tag,
  pushes to ghcr.io, attaches provenance + SBOM, and cosign-signs
  the digest-pinned image reference.
- **Task 13: structured logging** — new `core/logging_config.py`
  centralises structlog setup. `YT_UNIQ_LOG_FORMAT=json` flips to
  JSON renderer; `YT_UNIQ_LOG_LEVEL` controls level. Stdlib root
  logger is wired through structlog's `ProcessorFormatter` so
  existing `_log.warning` calls render through the same renderer
  without a big-bang rewrite.
- **Task 14: correlation IDs** — `RunOptions.run_id` auto-populates
  with `uuid7` (3.13+) / `uuid4` fallback. `run_full` wraps the
  caller's `on_event` to inject the ID into every `RunEvent.payload`.
  Web layer passes its locally-generated ID to `RunOptions.run_id`
  so HTTP response, SSE stream, and structured log all share one ID.
- **Task 15: `/readyz` + `/metrics`** — Prometheus families:
  `yt_uniq_segments_total{status}`, `yt_uniq_ffmpeg_failures_total`,
  `yt_uniq_runs_total`, segment + run duration histograms, active
  runs gauge, last pHash divergence gauge. `/readyz` returns 503 when
  no working encoder OR `work_dir` is read-only.
- **Task 16: rate limit + upload cap + audit log** — `slowapi`
  limiter keyed on basic-auth principal or client IP (default
  `30/minute` on `POST /api/run`); `ContentLengthLimitMiddleware`
  rejects >5 GiB requests with 413; `web/audit.py` appends JSONL
  lines on every state-changing request to `WebConfig.audit_log_path`.
- **Task 20: `yt-uniq run --dry-run`** — builds Plan + preflight +
  segment plan, prints encoder + ETA + disk estimate + first segment
  `filter_complex`, exits 0 without spawning ffmpeg.
- **Task 21: `--profile auto`** — `core/recommender.py` picks a
  shipped profile slug from source resolution + aspect ratio + HDR
  flag. Deterministic; CLI prints slug + one-line reason.

### Removed

- **Tasks 17–19** (Homebrew tap, winget manifest, Flathub manifest)
  intentionally dropped — distribution stays via GitHub Releases only
  per maintainer preference. Original tasks can be reinstated from
  git history of `.claude/plans/v1.0.1-to-v1.3-roadmap.plan.md` if
  the position changes.

## [v1.0.1] — 2026-06-14

Hotfix release. Backwards-compatible (no Plan/Profile field changes).
See `specs/v1.0.1-to-v1.3-roadmap.plan.md` for the full roadmap.

### Fixed

- **Keyframe-cache atomic write** — `_save_keyframe_cache` now uses the
  same `{pid}.{secrets.token_hex(4)}` tmp suffix + `fsync` + `os.replace`
  pattern as `encoder.py`. Two concurrent batch jobs writing the same
  source can no longer race on a PID-only tmp name and corrupt the cache.
- **Audio-chain `__IN__` guard** — `pipeline._wrap_chain_str` now raises
  `PipelineError` if a builder emits a naked `[in_label]` prefix in its
  `filter_str`. The video path already routed through this helper; the
  three audio wrap sites (FilterGraph, build_main_audio_command, and
  build_main_audio_command_windowed) now share the same guard.

### Added

- **Per-segment SHA-256 on resume** — `Segment` carries an optional
  `sha256` field; the orchestrator streams a digest after each
  successful encode and the checkpoint store re-verifies every `done`
  segment on resume. Truncated / corrupted segment files are demoted
  back to `pending` so the next run re-encodes only the broken segment.
  Pre-v1.0.1 state files (no `sha256`) are still accepted as-is.
- **Disk-space preflight** — new `_check_disk_space` rejects runs whose
  estimated output exceeds `free × 1.1` (`error`) and warns at
  `free × 1.5` (`warn`). Computed from source bitrate × duration × 1.3
  overhead; skipped when no `work_dir` is given. Walks up to the
  nearest existing ancestor so it can run before the orchestrator
  `mkdir`s.
- **Supply-chain quickwins** —
  `.github/dependabot.yml` (pip + actions + docker, weekly, grouped
  minor+patch); `.github/workflows/codeql.yml` (security-extended
  suite, weekly + on-push); `requirements-lock.txt` (pinned + hashed
  via `uv pip compile`, optional install path via `make dev USE_LOCK=1`
  and CI auto-prefers the lockfile when present).
- **Orchestrator final flush** — `run_full` now calls `store.flush()`
  after the segment loop so per-segment marks are persisted even on
  resume paths that skip the `set_main_audio` / `set_loudnorm` flush.

## [v1.0.0] — 2026-06-14

**First stable release.** API surface (`Plan`, `Profile`, `RunEvent`,
`RunOptions`, `RunSummary`) is frozen under SemVer; see
[`docs/versioning.md`](docs/versioning.md) for the breaking-change
contract and the RFC process for proposing changes.
[`SECURITY.md`](SECURITY.md) ships the disclosure policy.

No new user-facing features vs v0.9.0 — v1.0.0 is the production-ready
manifest of the work shipped across v0.5.5 through v0.9.0. Distribution
status: Linux AppImage signed; macOS/Windows shipped unsigned pending
code-signing credentials (see `docs/install.md`).

### Added

- **`docs/versioning.md`** — SemVer commitment, breaking-change
  definitions per contract (Plan / Profile / CLI / RunEvent / Python
  API), RFC process for proposing breaking changes.
- **`SECURITY.md`** — vulnerability disclosure policy, response
  timeline, supported-versions table, GitHub Private Vulnerability
  Reporting enabled.
- **`docs/api-contracts.md`** — field-by-field stability table for the
  public surface; stable / experimental / internal labels.
- **`tests/contracts/`** — snapshot tests for `Plan`, `Profile`,
  `RunEvent` serialisation. A diff means an intentional bump
  decision must be made before merge.
- **`.github/workflows/perf-regression.yml`** — nightly benchmark on
  ubuntu-latest; publishes JSON to gh-pages `perf-history/`; opens
  issue with label `perf-regression` on >15% degradation across two
  consecutive runs.
- **`docs/accessibility.md`** — WCAG 2.1 AA conformance statement,
  per-screen screen-reader manual test guide.
- **`installers/linux/AppImageBuilder.yml`** — signed AppImage with
  bundled ffmpeg static + Python 3.11 runtime.

### Changed

- **`pyproject.toml`** — `version` bumped to `1.0.0`; classifier
  `Development Status :: 5 - Production/Stable`.
- **`src/yt_uniquifier/__init__.py`** — `__version__` sourced from
  `importlib.metadata.version("yt-uniquifier")` (single source of
  truth in `pyproject.toml`).
- **Coverage gate** — CI enforces `--cov-fail-under=85` on `core/`,
  `--cov-fail-under=80` on `cli/`, `--cov-fail-under=75` on `gui/`.
- **mkdocs site** — versioned via `mike`; `latest` alias tracks the
  most recent tag.

### Security

- GitHub Private Vulnerability Reporting enabled. See `SECURITY.md`
  for disclosure timeline and supported-versions table.

## [v0.9.0] — 2026-06-14

Community + Web. Six atomic rounds; 906 unit + 27 GUI + 9 integration
smoke = 942 tests green; mypy --strict clean across 136 source files.

### Added

- **R1 / F9 — Profile marketplace** (`42fa5d3`). HTTPS-only,
  SHA-pinned, schema-validated. `yt-uniq profile {list-community,
  show, install, purge-cache, install-dir}`. GUI
  `CommunityProfilesDialog` in Profile Editor. 5-entry bootstrap
  catalogue shipped in the wheel.
- **R2 / F14 — Whisper subtitle transform** (`4901db7`).
  `core/transforms/video_subtitles.py` (filter-arg injection guards),
  `_whisper_probe.py` capability detection,
  `core/subtitles.py` whisper-cpp SRT generator cached by
  `(size, mtime, model, lang)`, `cli/cmd_subtitles.py`, preflight
  check `_check_subtitle_burnin`.
- **R3 — Opt-in local telemetry** (`96c6f48`). `core/telemetry.py`
  (off by default, append-only JSONL, HOME redaction, bounded
  rotation), `_maybe_record_telemetry` in orchestrator, CLI
  `yt-uniq telemetry {status,export,purge}` (no `enable`
  subcommand — by design), Settings groupbox + first-run consent.
- **R4 / F13 — Docker headless + web UI** (`f7e954f`).
  `src/yt_uniquifier/web/` FastAPI app (lazy import), SSE event
  streaming, vanilla-JS SPA, `yt-uniq-web` uvicorn launcher,
  multi-stage Dockerfile (ffmpeg + python + non-root + tini +
  healthcheck), docker-compose.yml for NAS deployments,
  `input_root` chroot + basic-auth gate.
- **R5 — Localization en + ru** (`dc85cc2`). `gui/i18n/`
  `RuntimeTranslator` subclass + in-Python catalogue (~40 keys),
  `AppState.locale` round-trip, Settings Language combo with
  hot-swap.
- **R6 — Documentation site (mkdocs-material)** (`7d61000`).
  `mkdocs.yml` strict mode, landing + getting-started + marketplace
  + web + telemetry + GUI walkthrough; `[docs]` extra;
  `.github/workflows/docs.yml` gh-pages deploy on tag.

### Fixed

- **Windows CI: LF line endings + redact_path separator tolerance**
  (`333017d`).

## [v0.8.0] — 2026-06-12

ML-grade QA + Plugin system. Seven rounds.

### Added

- **R1 — Transform plugin system** (`b664044`). Third-party
  transforms discoverable via
  `importlib.metadata.entry_points("yt_uniquifier.transforms")`.
- **R2 — SQLite-backed corpus** (`3dc9d0b`). `core/qa/corpus_db.py`,
  auto-migration from `index.json`, scale to 50k+ references.
- **R3 — PySceneDetect segment boundary mode** (`e5cf603`). Opt-in
  via `SegmentationConfig.mode="scene"` + `[scene]` extra.
- **R4 / F6 — SSCD ML-grade copy-detection metric** (`11fc8ea`).
  `core/qa/sscd.py`, lazy model download (~80 MB torchscript);
  HTML report shows SSCD distribution. `[ml]` extra.
- **R5 / F11 — Per-segment VMAF target-quality feedback loop**
  (`6262704`). `Profile.target_vmaf*`, `segmenter._encode_once`
  retry on miss, `RunEvent` kinds `target_vmaf` /
  `target_vmaf_failed`.
- **R6 — Calibrate via SSCD metric** (`1e4e71c`).
  `calibrate(metric="sscd")`, CLI `--metric` flag, GUI dropdown.

### Documentation

- **R7 — docs + cross-cutting smoke + master-plan checkboxes**
  (`2f969d5`).

## [v0.7.0] — 2026-06-08

GUI maturity + Platform profiles. Eight rounds; 821 tests green;
ruff + mypy clean; WCAG-AA contrast guard catches 8 real failures
that were then fixed.

### Added

- **R3 / F3 — `video.fit_aspect` transform + 7 platform profiles**
  (`4dedf55`). `youtube_4k`, `youtube_1080p`, `youtube_shorts`,
  `tiktok_vertical`, `instagram_reels`, `instagram_square`,
  `linkedin_square`. Crop / pad_blur / pad_black modes.
- **R4 / F2 / F7 — Live pHash divergence indicator + Auto-tune
  button** (`d4d3556`). pHash sampling in orchestrator
  (`RunOptions.sample_phash: off|light|full`), custom-painted
  `_Sparkline` widget with EMA, KPI-banded colours; `🎯 Auto-tune`
  CTA launches `CalibrateWorker`.
- **R5 / F4 — Post-job notifications** (`d58726b`). `core/notifications.py`
  auto-detects Discord embed / Slack blocks / Telegram Markdown
  / generic; stdlib-only (urllib + smtplib); optional `keyring`
  for SMTP password; Settings groupbox + Test button.
- **R6 / F5 — Pause / Resume** (`0dcdb12`).
  `core/process_control.py` (POSIX SIGSTOP/SIGCONT + Windows lazy
  psutil, recursive tree), `PauseToken` in runner with 24 h
  auto-cancel, `paused_at` marker in `state.json`, GUI `&Pause`
  button (Space shortcut) in Run screen.

### Changed

- **R1 — type-ignore sweep + `QStandardPaths` + theme tokens +
  `importlib.resources`** (`cbf243a`). All `# type: ignore`
  removed (C1-C7). `QStandardPaths.AppConfigLocation` for config,
  `CacheLocation` for cache; migration helper copies from legacy
  `~/.config/yt_uniquifier/`. Theme leaks fixed (`kpi_pills`,
  `preflight_panel` subscribe to `state.theme_changed`).
  Profiles loaded via
  `importlib.resources.files("yt_uniquifier").joinpath("profiles")`
  for PyInstaller portability.
- **R2 — Accessibility framework + Run/Settings sweep + global
  excepthook** (`5959ce5`, `dc3d237`). `setAccessibleName/Description/
  setTabOrder/setShortcut` (Ctrl+R / Esc / Ctrl+Q / Ctrl+1..0);
  mnemonics in all CTAs; global `sys.excepthook` → QMessageBox +
  "Copy details" + 100 KiB `crash.log` rotation;
  `KeyboardInterrupt` bypass.

### Fixed

- **R7 — docs catch-up, WCAG-AA contrast guard, real-ffmpeg
  pause/resume integration** (`e09bcd9`). `tests/gui/test_theme_contrast.py`
  caught 8 real contrast failures across badge / KPI tokens
  (fg_dim light, badge_ok, kpi_yellow/green, per-band fg);
  `tests/integration/test_pause_resume_real_ffmpeg.py` (2 cases:
  pause-mid-encode → resume duration parity; pause_token wired
  but unused → no-op).
- **R8 — CI split + per-test timeout + isolated config dir**
  (`3c880fb`). pytest-timeout 3-minute cap;
  `XDG_CONFIG_HOME=tmp` in CI prevents user-config pollution.
- **R9 (rounds 1-6) — Windows-matrix unblock** (`96b240f`,
  `abb3ca5`, `0093e38`, `2f1d8e2`, `ad9f69b`, `3bbe756`). Linux
  silent-subprocess cancel; Windows `_pid_alive` safety;
  `_FakePopen` context-manager protocol; `proc.terminate` always
  runs after `taskkill` attempt.

## [v0.6.0] — 2026-06-03

Performance + Distribution. Three rounds + CI matrix expansion.

### Added

- **F8 — Vulkan AV1 encoder** (`ae4ef3d`). `av1_vulkan` in
  `_CANDIDATES` (+ `EncoderKind="av1"`, `EncoderVendor="vulkan"`).
  AMD/Intel GPU AV1 without NVENC/QSV.
- **F1 R1 — release workflow scaffolding** (`9c1f3bf`). 3-OS
  matrix, `softprops/action-gh-release` draft on tag push,
  unsigned PyInstaller artifacts. R2-R5 (notarytool, signtool,
  AppImage, release automation) deferred to v1.0.0.
- **windows-latest in CI matrix** (`eff5d3d`). 3 OS × 2 Py = 6
  jobs. `@needs_symlink` skip marker for `os.symlink` tests
  (Win admin required).

### Performance

- **R1 — five mechanical perf wins** (`a76293b`):
  - **B1** keyframe-cache key → `(st_size, st_mtime_ns)`; `md5_file`
    removed from hot path. 180 GB 4K HDR cold-start: 60-360 s → <1 ms.
  - **B2** loudnorm pass-1 prepended with
    `aresample=16000,aformat=channel_layouts=mono`. 4 h source:
    144 s → 24 s (6×).
  - **B5** `auto_subsample_for_duration` helper + reorder in
    `build_report` (probe before VMAF). 4 h 24 fps:
    4-11 h → ~1 h (6-11×).
  - **B6** `ThreadPoolExecutor` parallels 10 candidate probes.
    Cold-start 5.5 s → 0.6 s (9×).
  - **B7** NVENC multi-GPU sum — `_nvenc_max_parallel` sums sessions
    across all GPUs. Dual A6000: 8 → 16 sessions.
- **R2 — debounced flush + NFS cursor** (`ae4ef3d`):
  - **B4** `_flush_maybe` via 10 marks OR 0.25 s thresholds;
    phase-boundary writes force-flush. 1000-segment × 4 workers:
    ~4000 fsync → ~100.
  - **B8** NFS lease cursor — `_lease_cursor` cached, refreshed on
    exhaust. ~5× fewer `iterdir` round-trips.
- **R3 — fused single-fork segment encode** (`d37e6fd`):
  - **B3** `build_video_segment_command_fused`. Env-flag
    `YT_UNIQ_DISABLE_FUSE=1` for emergency rollback.

## [v0.5.5] — 2026-06-01

Hotfix sprint. Ten A-fixes + 1 bonus + 89 regression tests. No new
user-facing features; all changes additive or behaviour-preserving.
Production-critical bug audit results.

### Fixed

- **A1 — `RunEvent.payload` data race in parallel segmentation**
  (`d719221`). New `RunEvent` per emission instead of mutating
  shared payload dict; data race in `segmenter.py:265-280` killed.
- **A2 — `assert isinstance` → `ensure_params`/`ensure_rng`**
  (`abe722e`). Survives `python -OO` and PyInstaller
  `--optimize=2`. Inline raises in `pipeline.py` (4 sites) and
  `runner.py` (1 site). Regression test under `PYTHONOPTIMIZE=2`.
- **A3 — Resume edge case: per-segment recovery + defensive
  `.exists()` filter in concat** (`abe722e`). 2 integration tests
  (all-missing + partial cleanup).
- **A4 — Cross-platform PID lockfile in `CheckpointStore`**
  (`cdb23fe`). Cross-host via `os.kill(pid, 0)`. 8 unit tests
  including subprocess collision.
- **A5 — Cancel watcher in `runner.py::_run_once`** (`58fe715`).
  Daemon thread; cancel during silent ffmpeg in <7 s instead of
  the 3600 s `communicate()` timeout. 3 timing tests.
- **A6 — `cancel_token` plumbed through calibrate / qa /
  encoder.detect_encoders** (`58fe715`). `CorrelateWorker`
  rewritten on `Popen` + `poll`. 6 regression tests.
- **A7 — `lease()` rejects symlinks** (`cdb23fe`). Marker log
  on rejection.
- **A8 — Reaper double-checks heartbeat mid-loop** (`cdb23fe`).
  Closes worker-revive race.
- **A9 — Pillow pin `>=10.3.1,<11`** (`d719221`). CVE-2024-28219
  (heap buffer overflow in `_imagingcms`) closed for fresh installs.
- **A10 — `model_config = ConfigDict(extra="forbid")` across all
  19 `*Params`** (`d719221`). Registry-walking regression test
  catches accidental relaxation.
- **5.2 bonus — `_safe_host_name()` sanitisation** (`cdb23fe`).
  Path traversal via hostname blocked.

### Changed

- **`ruff polish` — Annotated quote drops + suppress() rewrites**
  (`cf0bb9f`).

## [v0.5.4.1 / Unreleased pre-hotfix] — 2026-05-31

Post-v0.5.4 hardening — robustness, concurrency, and lifecycle fixes
surfaced by two rounds of internal audit. No new user-facing features;
all changes additive or behaviour-preserving. Folded into the v0.5.5
hotfix sprint shortly after.

### Added

- **`core/preflight.py::_check_rubberband_perf`** — preflight WARN
  (`audio.pitch.rubberband.slow`) when a rubberband-enabled profile
  runs on a source `>60 s` or `>1080p`. The 2026-05-31 matrix §9
  measured cid_aware/cid_aggressive at 10–15× wall time vs
  soft/medium on 4K and 5-min content, all hitting the 1800 s
  ceiling. Severity=warn so the encode still proceeds; suggestion
  points at `method='asetrate'` for throughput-sensitive batches.
  +5 unit tests + 1 order-guard test. Backed by a measured wall-time
  matrix in `docs/profiles.md#rubberband-performance-characteristic`.
- **`core/audio_windows.py::verify_audio_filters_available`** —
  defense-in-depth re-probe of `rubberband` filter availability
  immediately before the audio chain runs. Closes the
  preflight-vs-runtime window that burned 18 min of video work on
  the 2026-05-31 matrix incident (preflight cache reported the
  filter present, runtime ffmpeg threw "No such filter" mid-encode).
  Wired into `core/segmenter.py::process_main_audio` before
  `run_ffmpeg`; raises `PipelineError` with a clear remediation if
  the filter cannot be opened. +3 unit tests.
- **`tools/gui_sweep.py`** — developer-only harness that drives
  `MainWindow` through all 10 screens on the real Qt platform
  (refuses `QT_QPA_PLATFORM=offscreen`), captures PNG per screen +
  Qt message log + a `report.md`. Companion to the offscreen
  visual regression suite (`tests/visual/test_gui_screenshots.py`)
  for compositor / font / HiDPI issues the offscreen baseline
  can't surface. Optional `--smoke-worker` flag fires a tiny
  `RunWorker` against `clip_a × soft.yaml` to exercise the
  worker bus end-to-end.
- **`docs/gui_sweep.md`** — manual per-screen sweep checklist
  (setup + 10 screens × interactions/verify/watch-for + triage
  table). Closes the GUI-deep-sweep backlog item from the
  2026-05-31 matrix triage.

- **`core/transforms/hdr_wrap.py`** — pre-zscale even-dim guard for the
  `keep_hdr=true` color-wrap path. zscale on `yuv420p10le` rejects odd
  dimensions with `code 1027: image dimensions must be divisible by
  subsampling factor`; geometric transforms upstream (`video.crop_resize`,
  `video.rotate`) can produce odd dims that the chain's final
  even-dim guard catches for the encoder but reaches zscale uncorrected.
  `wrap_linear` now prepends `scale=trunc(iw/2)*2:trunc(ih/2)*2` so
  zscale always sees even dims. Found 2026-05-31 once a real
  zimg-enabled ffmpeg (evermeet.cx static build) made the HDR
  keep-path actually exercisable. +1 unit test.
- **`core/preflight.py`** — zscale dry-run probe spec now specifies
  input colorspace flags (`tin=bt709:min=bt709:pin=bt709`) so the
  probe distinguishes "zscale absent" from "zscale present but
  rejecting testsrc2's untagged colorspace". Verified against both a
  no-zimg ffmpeg (correctly reports missing) and a zimg-enabled
  ffmpeg (correctly reports present).
- **`core/preflight.py`** — `video.tonemap_sdr` profile against HDR
  source used to crash mid-encode (~10-30s in) with "No such filter:
  zscale" on ffmpeg builds without zimg. New `tonemap.zscale.missing`
  preflight FAIL catches this at second zero. Found by post-fix
  matrix re-run on 2026-05-31 (cid_aware_hdr_to_sdr × HDR-input cell);
  symmetric to the rubberband gap fixed earlier in the same pass.
  +1 regression test.
- **`core/preflight.py`** — SDR source against an HDR-to-SDR profile
  (e.g. `cid_aware_hdr_to_sdr` applied to plain BT.709 content) used to
  crash mid-encode with a cryptic `Could not open encoder before EOF`
  ffmpeg error. New `_check_tonemap_sdr_input` check emits a clear
  preflight FAIL (`tonemap.sdr_input`) at the start of the run. Found
  by `tools/real_video_matrix.py` sweep against 14 synthetic inputs —
  affected 7/99 cells. See `docs/bug-triage-2026-05-31.md` for full
  triage.
- **`tests/visual/test_gui_screenshots.py`** — History screen excluded
  from byte-equal snapshot comparison; the screen reads a persistent
  run-log store whose rows accumulate between baseline capture and
  replay, so it flakes on any developer machine that has actually used
  the CLI/GUI. Same treatment as Validation. Existence + non-empty
  check still runs.
- **`core/encoder.py`** — race condition in shared encoder cache writes;
  atomic write now uses a `os.getpid()`-suffixed temp name to avoid
  cross-process collisions during parallel `yt-uniq batch`.
- **`core/orchestrator.py`** — idempotent resume; in-progress segments
  are reset to `failed` on worker crash so the next run picks them up.
- **`core/runner.py`** — `CancelToken` backed by `threading.Event` for
  cross-thread safety.
- **`core/segmenter.py`** — keyframe cache atomic write with `fsync` +
  pid suffix; restore `OMP_NUM_THREADS` after parallel batch.
- **`core/pipeline.py`** — HDR linear wrap now applied on the segmented
  video path (was missing on resume); dedicated rng seed for windowed
  loudnorm jitter.
- **`core/preflight.py`** — `_check_tonemap_order` runs unconditionally;
  lock-guarded `_FFMPEG_FILTERS_CACHE`.
- **`core/calibrate.py`** — `quality=None` now fails the quality gate.
- **`core/corpus.py`** — inter-process `flock` + `fsync` atomic write
  for `index.json`; precomputed `phashes` / `audio` accepted by
  `search_match` for batch speedup.
- **`core/seed_resolver.py`** — `per_file` strategy uses `as_posix()`
  for cross-platform stable seeds.
- **`gui/`** — workers cancelled in `MainWindow.closeEvent`; RunWorker
  signals disconnected before replacement; `CorpusListWorker` quit +
  waited before dropping the Python ref; Corpus list / validation
  correlate / EncoderSelector populated off the GUI thread; AppState
  persisted immediately on `push_recent`; ChartWidget appends
  incrementally (was O(N²) rebuild); Preflight ↔ Run share a single
  `Plan`.
- **`pyinstaller/`** — `collect_submodules` for `transforms` + `gui`;
  dropped deprecated `block_cipher`.
- **CI** — Qt6 system dependencies installed on Ubuntu runners
  (`libegl1`, `libxcb-*`, etc.) so PyQt6 imports succeed under
  `QT_QPA_PLATFORM=offscreen`.

### Changed

- `make lint` aligned with CI (`ruff check .`, was `ruff check src/`).

## [v0.5.4] — 2026-05-29

GUI phase 25 (final): Settings + Corpus screens + PyInstaller packaging.

### Added

- **Settings screen** — theme switch (dark/light), default profile path,
  reset-cache buttons (encoder cache, keyframe cache, app state).
- **Corpus screen** — list / add / remove fingerprints from the local
  corpus index used by `cid_predict_vs_corpus`.
- **PyInstaller packaging** — `pyinstaller/yt-uniq-gui.spec` builds
  `dist/yt-uniq-gui.app` on macOS (~250 MB), `dist/yt-uniq-gui/` on
  Windows / Linux. `make build` is the canonical entry point.

## [v0.5.3] — 2026-05-29

GUI phase 24: Queue dashboard + Validation wizard.

### Added

- **Queue dashboard** — list/init/add/status/reset on a shared-FS queue
  (the GUI face of `yt-uniq queue` + `yt-uniq worker`).
- **Validation wizard** — 3-step UI for the real-CID validation harness
  (Generate variants → Upload to YouTube → Correlate observations).

## [v0.5.2] — 2026-05-29

GUI phase 23: QA Viewer + Profile Editor + History.

### Added

- **QA Viewer screen** — embedded `QWebEngineView` for `<out>.qa.html`
  with graceful "Open in browser" fallback when WebEngine is absent
  (e.g. `QT_QPA_PLATFORM=offscreen`). Standalone QA pair mode
  (`yt-uniq qa` equivalent) also lives here.
- **Profile Editor** — YAML profile editing with live schema validation
  via the pydantic `Profile` model.
- **History screen** — last 100 runs persisted to
  `~/.config/yt_uniquifier/history.json`.

## [v0.5.1] — 2026-05-29

GUI phase 22: Batch + Calibrate screens.

### Added

- **Batch screen** — directory drag-drop → sequential file processing.
- **Calibrate screen** — bisect-intensity wizard with the new
  `ChartWidget` for convergence visualisation (uses
  `PyQt6-Charts~=6.7` if installed; falls back to a custom paint event).

## [v0.5.0] — 2026-05-29

GUI phase 21: PyQt6 desktop foundation.

### Added

- **PyQt6 desktop shell** — `MainWindow` with `QListWidget` sidebar +
  `QStackedWidget` content area (Slack / Discord / VSCode style).
- **AppState** — single source of truth, persisted to
  `~/.config/yt_uniquifier/state.json`. Screens subscribe via Qt signals.
- **Run screen** — drag-drop input → auto-probe → preflight → Run with
  segment-timeline progress and KPI pills.
- **Worker contract** — `WorkerBase(QThread)` with `started_`,
  `finished_ok`, `failed`, `log`, `progress` pyqtSignals + cooperative
  `CancelToken`. All long-running operations route through this base.

### Changed

- Legacy single-window file picker (260 LOC `gui/app_pyqt.py`) replaced
  by the new shell. CLI behaviour unchanged.

## [v0.4.3] — 2026-05-29

Phase 20: opt-in bitstream sanitization.

### Added

- **`--sanitize-bitstream` flag on `yt-uniq run`** — second-pass
  `libx264` normalization that strips encoder-specific bitstream
  signatures (NVENC / QSV / AMF / VideoToolbox). Default off (adds
  wall time + costs ~3 VMAF).

### Changed

- Output metadata stripped of `encoder=yt-uniquifier/X` (the v0.3.x
  default that fingerprinted output as tool-generated).

## [v0.4.2] — 2026-05-29

Phase 19: per-segment audio divergence.

### Added

- **Windowed audio chain** — `process_main_audio` now splits the source
  into 60s windows, applies the audio transform stack with per-window
  seeds derived via the divergent strategy, and stitches with
  `acrossfade` seams. Global EBU R128 loudnorm stays a single pass
  over the whole source so loudness footprint remains stable.
- New QA KPI: `audio_fp_hamming_per_window_variance` — measures
  per-window divergence (target ≥ 4 bits between adjacent windows).

## [v0.4.1] — 2026-05-29

Phase 18: real-CID validation harness.

### Added

- **`docs/validation_harness.md`** — manual upload-observe-record loop
  to validate the predictor against actual YouTube Content ID outcomes.
- **`tools/generate_variants.py`** — produces N variants from one
  source for batch upload + observation.
- **`tools/validation_log.csv` schema** — append-only log linking
  predicted `cid_predict_self` / `cid_predict_vs_corpus` to observed
  CID claim outcomes.

No production code change; harness is docs + helper script only.

## [v0.4.0] — 2026-05-29

Phase 17: quick wins + Poisson temporal_jitter + subpixel_sharpen.

### Added

- **`video.subpixel_sharpen` transform** — `unsharp` with small radius
  + low strength; shifts pHash without visible quality cost.
- **Poisson-distributed `video.temporal_jitter`** — replaces the
  v0.3.3 strictly-periodic `geq=if(eq(mod(N,30),OFFSET)…)` with
  Poisson event times so frequency analysis / 30-frame-stride sampling
  no longer defeat it (Fojcik & Syga 2025 used Poisson events).

### Changed

- Stripped `-metadata encoder=yt-uniquifier/X` from output — was a
  file-level signature visible to any metadata heuristic.
- Dropped placebo `audio.resample 48000 → 47999 → 48000` (0.002 %
  shift, below chromaprint quantization, no measurable audio FP delta).
- Bumped weak defaults in `cid_aware`: `video.color_eq.brightness`
  0.015 → 0.025, `video.noise.strength` 5 → 8, `audio.eq` band gain
  ±0.6 dB → ±1.0 dB.
- 18 → 19 registered transforms total.

### KPI targets

- pHash similarity (mean) < 0.70 (was < 0.75).
- pHash worst chunk < 0.75 (was < 0.80).
- VMAF mean ≥ 83 (slight relaxation accepted; stronger transforms cost
  ~2 VMAF points).

## [v0.3.3] — 2026-05-27

Four academically-verified Content-ID resistance mechanisms layered on the
v0.3.2 baseline.

### Added

- **`video.temporal_jitter` transform** — periodic frame blackout + drop
  on rng-randomized phase. Default-enabled in `cid_aware` (`blackout_prob=0.033`,
  `drop_prob=0.020`). Source: Fojcik & Syga, arXiv:2501.11171 (2025) —
  random frame perturbation drops μAP 60 %+ on Meta VSC2022 baseline.
- **`audio.noise_overlay` transform** — parametric pink/white/brown noise
  mixed via `anull` + `anoisesrc` + `amix`. Opt-in via `cid_aggressive` only
  (`noise_db=-12.0`, color=pink). Source: Smitelli 2010 — ≥45 % mix breaks
  CID; -12 dB stays well below the destructive threshold while shifting
  chromaprint sub-fingerprints.
- **Audio FP Hamming distance as QA KPI** — new fields
  `audio_fp_hamming_per_frame` and `audio_fp_match_confidence` on `QAReport`.
  Computed as XOR + popcount over paired chromaprint subfingerprints.
  Surfaced in the HTML report with a heuristic interpretation
  (≥30 bits = high-confidence non-match).
- **`seed_strategy: divergent`** — new value on `Profile.seed_strategy`.
  Per-run base seed + per-segment seed derived via
  `sha256(plan_hash, segment_idx, run_seed)`. Adjacent segments now get
  distinct transform-parameter phases. Both `cid_aware` and
  `cid_aggressive` profiles ship with this strategy.

### Changed

- `cid_aware` and `cid_aggressive` enable `video.temporal_jitter` by default.
- `cid_aggressive` enables `audio.noise_overlay` by default.
- 16 → 18 registered transforms total.

### Tests

- +24 new tests (temporal_jitter 7, audio_fp_hamming 8, divergent_seed 6,
  audio_noise_overlay 6, regression −3).
- Final suite: 365 passing, 1 skipped (GUI smoke when PyQt6 present).
- `ruff check` clean, `mypy --strict` clean across 68 source files.

## [v0.3.2] — 2026-05-27

Critical hotfix closing the Smitelli ±5 % pitch CID match-zone.

### Fixed

- `cid_aware.audio.pitch_tempo.pitch` bumped 1.04 → 1.06 — was inside
  Smitelli's documented CID match zone (≤ ±5 %); now past +5 % boundary.
- `cid_aggressive.audio.pitch_tempo.pitch` bumped 1.06 → 1.08 for
  comfortable margin.

### Added

- **`audio.haas_stereo` transform** — `adelay=0|N` mono-compatible variant
  of stereo phase inversion (Smitelli 2010 showed full inversion breaks
  audio CID). 15 ms in `cid_aware`, 25 ms in `cid_aggressive`.
- `docs/profiles.md` — Smitelli citation table documenting verified
  pitch / noise / phase thresholds.

### Changed

- README: 13 → 16 transforms, v0.3.2 release marker.
- Regression-guard tests on `cid_aware.pitch ≥ 1.06` and `cid_aggressive.pitch ≥ 1.08`.

### Tests

- +8 new (haas_stereo 6, threshold regression 2). Suite green at 340 passing.

## [v0.3.1] — 2026-05 (phase 14)

Audio CID resistance hotfix following post-v0.3.0 OSS-competitor research:
long-form CID leans on audio more than video, and `calibrate` was running
against a fragile VMAF-only quality target.

### Added

- **`audio.compand` transform** — dynamic-range jitter (per-run randomized
  threshold/ratio). Breaks the audio-envelope fingerprint that chromaprint
  hashes.
- **`audio.reverb` transform** — `aecho` presets (small_room, medium_room,
  hall, plate). Opt-in via `cid_aggressive`.
- **Loudnorm target jitter** — `audio.loudnorm.target_jitter_lufs` (±LUFS
  per run). The loudness footprint is no longer a stable signal across
  uploads.
- **Quality fallback chain in `calibrate`** — VMAF → SSIM × 100 → pHash × 100
  on a unified 0..100 scale. Fixes calibration on machines without libvmaf.

### Changed

- `audio.pitch_tempo` defaults to formant-preserving `rubberband` method
  (was `asetrate` + `atempo` cascade). Voice stays natural at larger shifts.
- `cid_aware.audio.pitch_tempo.pitch`: 1.012 → 1.04 (intended past CID
  threshold; later revealed to still be inside Smitelli's ±5 % match zone,
  fixed in v0.3.2).

## [v0.3.0] — 2026-04 (phases 11–13)

HDR→SDR tonemap, parallel encoding, distributed batch on a shared filesystem.

### Added

- **`video.tonemap_sdr` transform** — zscale linearize → tonemap (hable /
  reinhard / mobius / aces) → SDR. Enables HDR-source content to be
  uniquified into SDR output.
- **Parallel GPU/CPU encoding** — `--workers N` flag on `yt-uniq run`.
  Each `EncoderCandidate` carries its own `max_parallel` cap (NVENC
  consumer = 3, NVENC pro = 8, CPU = ½ cores, GPU vendors auto-detected).
- **Distributed batch** — `yt-uniq queue` + `yt-uniq worker` commands.
  Atomic POSIX-rename leasing on a shared filesystem (NFSv4 with `noac`,
  ZFS, ext4). Heartbeat + reaper for failed workers. No redis / no DB.
- `cid_aware_hdr_to_sdr.yaml` profile.

### Tests

- 240 tests passing across all phases.

## [v0.2.0] — 2026-02 (phases 6–10)

Real Content-ID resistance: real HDR pipeline, audio strength, calibrate
loop, corpus collision check, scale validation.

### Added

- **Real HDR pipeline** — `video.color_eq` and `video.noise` wrap through
  a zscale linear-light roundtrip when source is HDR (PQ / HLG) and
  `keep_hdr=true`. Math is meaningful only in linear light.
- **Strong audio transforms** — `audio.spectral_smear` (mild chorus),
  `audio.resample` (intermediate non-standard SR + back), `audio.eq`
  band randomization.
- **`seed_strategy`** field on `Profile`: `fixed` / `per_run` / `per_file`.
  Per-run variability for batch uploads of N variants.
- **`cid_predict`** — chunked per-4-sec phash + audio Jaccard, predicts
  CID self-match. Drives the HTML heatmap and the calibrate target.
- **Corpus** — `yt-uniq corpus add/list/remove`. QA reports flag matches
  against previously-uploaded files.
- **`yt-uniq calibrate`** — bisect intensity to hit a CID self-match target
  without crashing quality.
- **Scale validation** — keyframe cache (`~/.cache/yt_uniquifier/keyframes/`,
  30-day TTL), parallel segment processing, adaptive QA stage sampling.
- `cid_aware.yaml`, `cid_aggressive.yaml`, `medium_hdr.yaml` profiles.

### Changed

- README, docs, and specs rewritten around the v0.2 roadmap.

## [v0.1.0] — 2025-12 (phases 0–5)

Foundation pipeline: probe, transforms, segmenter, QA, GUI.

### Added

- Project bootstrap (`pyproject.toml`, ruff + mypy strict, CI on Ubuntu +
  macOS for Python 3.11 / 3.12).
- `core/probe.py` — single ffprobe call → `SourceMeta` with HDR / chapter /
  language tag detection.
- `core/encoder.py` — multi-vendor encoder detect with real test-run
  (NVENC, QSV, AMF, VideoToolbox, libx264, libx265). Cached in
  `~/.cache/yt_uniquifier/encoders.json`.
- 9 initial transforms: `video.crop_resize`, `video.rotate`,
  `video.color_eq`, `video.noise`, `video.blend_b`, `video.speed`,
  `video.mirror`, `audio.pitch_tempo`, `audio.eq`, `audio.loudnorm`.
- `core/pipeline.py` — `FilterGraph.build()` composes one `-filter_complex`
  per ffmpeg invocation. No `bgr24` round-trip through Python stdin.
- `core/segmenter.py` — keyframe-aware split → per-segment process →
  concat demuxer. Resume via atomic `state.json`.
- `core/runner.py` — subprocess wrapper with `-progress pipe:1`,
  `RunEvent` stream, `CancelToken`.
- `core/preflight.py` — YouTube target matrix + HDR validation.
- `core/qa/` — pHash, audio fingerprint (chromaprint), VMAF, SSIM, HTML
  report with jinja2 template.
- CLI: `yt-uniq version | probe | run | preflight | qa | batch`.
- GUI: PyQt6 desktop UI as a thin `core.orchestrator.run_full` consumer.
- Profiles: `soft.yaml`, `medium.yaml`, `aggressive.yaml`, `legacy_ab.yaml`.
- Docs: architecture, profiles, filter graph, YouTube targets.

## [baseline] — pre-v0.1.0

Legacy `Video-Deduplicator` prototype: PyQt5 single-window app, pipeline
embedded in a `QThread`, frame blending via raw `bgr24` over
`subprocess.stdin`. Replaced by the v0.1.0 rewrite.

[Unreleased]: https://github.com/Hostlife22/yt_uniquifier/compare/v0.5.4...HEAD
[v0.5.4]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.5.4
[v0.5.3]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.5.3
[v0.5.2]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.5.2
[v0.5.1]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.5.1
[v0.5.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.5.0
[v0.4.3]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.4.3
[v0.4.2]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.4.2
[v0.4.1]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.4.1
[v0.4.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.4.0
[v0.3.3]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.3
[v0.3.2]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.2
[v0.3.1]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.1
[v0.3.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.0
[v0.2.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.2.0
[v0.1.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.1.0
