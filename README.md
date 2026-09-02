# yt-uniquifier

> Production-grade re-encoder with controlled, calibrated micro-transforms for
> owned or licensed video content. **Current source version: v1.3.1** — stable API
> under SemVer, signed-ready Linux AppImage + unsigned macOS / Windows
> bundles, WCAG 2.1 AA desktop GUI, optional FastAPI web UI + Docker image,
> third-party plugin system, community profile marketplace, opt-in local
> telemetry, English + Russian localization.

📚 **Live docs**: <https://hostlife22.github.io/yt_uniquifier/> — mkdocs-material site, auto-deployed on every `v*` tag.

![yt-uniquifier Run screen](./docs/screenshots/run-screen.png)

## What it does

- **`yt-uniq` CLI** (13 subcommands), **`yt-uniq-gui`** PyQt6 desktop (10
  screens, full keyboard nav), and **`yt-uniq-web`** headless FastAPI
  server on top of `ffmpeg`.
- **20+ micro-transforms** composed into a single `-filter_complex` per
  ffmpeg invocation: crop+rescale, color jitter, noise, rotation, mirror,
  frame-blend, HDR→SDR tonemap, **temporal frame jitter**
  (Fojcik & Syga 2025), pitch / tempo (formant-preserving rubberband),
  EQ, audio resample, spectral smear, compand (dynamic-range jitter),
  reverb, Haas stereo widening, **parametric noise overlay**
  (pink/white/brown), EBU R128 loudness normalization with target jitter,
  Whisper-driven soft subtitle inject.
- **Third-party plugins**: transforms register via the
  `yt_uniquifier.transforms` entry-point group; trust model + bootstrap
  in [`docs/plugins.md`](./docs/plugins.md).
- **Keyframe-aware split** + optional **content-aware scene-cut split**
  → per-segment process → concat demuxer, so multi-hour files survive
  Ctrl+C and resume from `state.json` on the next run.
- **Per-segment VMAF target** with bounded retry (configurable per
  profile) — automatic quality floor without manual re-runs.
- **Multi-track audio**, soft subtitles, and chapters passed through.
- **Real HDR support** via zscale linear-light wrap when keeping HDR,
  or via `video.tonemap_sdr` (hable / reinhard / mobius / aces) when
  targeting SDR.
- **Multi-vendor encoder detect** (NVENC / QSV / AMF / VideoToolbox /
  libx264 / libx265) with real test-run on a null source; each candidate
  carries its own `max_parallel` cap.
- **Per-run variability**: every invocation rolls a fresh `run_seed`,
  so two runs of the same profile against the same source produce
  different fingerprints — useful for uploading N distinct variants.
- **Content-ID-aware QA**: chunked per-4s pHash + audio Jaccard
  predictor + optional **SSCD** semantic-similarity model
  ([`docs/sscd.md`](./docs/sscd.md)), optional check against a local
  **corpus** of previous uploads. HTML report with per-chunk heatmap.
- **Automated calibration** (`yt-uniq calibrate`): bisects profile
  intensity against a target `match_probability`, with optional SSCD
  metric for semantic-similarity tuning.
- **Distributed batch** via shared filesystem: `yt-uniq worker` drains
  a queue across N machines using atomic POSIX rename leasing —
  **no redis, no database**, just NFSv4 with `noac` (or ZFS / ext4).
- **Profile marketplace** (`yt-uniq profile install <slug>`): HTTPS +
  SHA-256-pinned + schema-validated download from a community catalog.
- **Opt-in local telemetry** — JSONL events written to your config dir,
  no network egress in v1.0; full schema in
  [`docs/telemetry.md`](./docs/telemetry.md).
- **English + Russian UI** (`QTranslator`, hot-switch in Settings).
- **WCAG 2.1 AA** accessibility: visible focus outlines, keyboard
  reachability on every control, screen-reader-friendly names.
  Conformance statement: [`docs/accessibility.md`](./docs/accessibility.md).

## What it is NOT

A tool to evade rights-holder detection of third-party copyrighted material.
The intended scenarios are: re-uploading your own content, distributing
licensed material in multiple cuts, or producing fair-use derivatives. If your
use case is "make YouTube Content ID stop matching someone else's movie" —
this is the wrong tool, and I won't help you wire it up.

## Install

Requires Python 3.11+ and `ffmpeg` / `ffprobe` on `PATH`.

### Pre-built installers (v1.0.0)

| OS      | Format       | Signing                       | Where                                          |
|---------|--------------|-------------------------------|------------------------------------------------|
| Linux   | `.AppImage`  | ✅ self-contained + SHA256SUMS | GitHub Releases → `yt-uniq-gui-*.AppImage`     |
| macOS   | `.app.zip`   | ❌ unsigned (Gatekeeper bypass) | GitHub Releases → `yt-uniq-gui-macOS.zip`      |
| Windows | `.zip`       | ❌ unsigned (SmartScreen bypass)| GitHub Releases → `yt-uniq-gui-Windows.zip`    |

Per-OS bypass + SHA256SUMS verification: [`docs/install.md` § 0](./docs/install.md).
macOS / Windows code signing will land as a v1.0.x patch once credentials are
in place (see [`installers/README.md`](./installers/README.md)).

### From source (developers / contributors)

```bash
git clone https://github.com/Hostlife22/yt-uniquifier.git && cd yt-uniquifier
make dev                           # .venv + pip install -e ".[dev,gui]"
yt-uniq-gui                        # GUI; or `yt-uniq run <input> ...` for CLI
```

**Extras** (compose to taste):

| Extra        | Adds                                                                |
|--------------|---------------------------------------------------------------------|
| `[dev]`      | pytest, ruff, mypy, coverage, benchmark deps                        |
| `[gui]`      | PyQt6 + WebEngine for `yt-uniq-gui`                                 |
| `[gui-charts]` | PyQt6-Charts for divergence sparkline + KPI widgets               |
| `[qa]`       | chromaprint (`pyacoustid`) for audio fingerprinting                 |
| `[scene]`    | PySceneDetect for content-aware segmentation                        |
| `[ml]`       | torch + torchvision (CPU) for SSCD semantic-similarity QA           |
| `[web]`      | FastAPI + uvicorn for `yt-uniq-web`                                 |
| `[docs]`     | mkdocs-material for building the docs site locally                  |

Optional native binaries (graceful skip / fallback when missing):

- `fpcalc` (chromaprint) — audio fingerprint similarity & corpus matching
- ffmpeg with `libvmaf` — VMAF score (and target-VMAF bounded retry)
- ffmpeg with `zscale` (zimg) — HDR-keep wrap, HDR→SDR tonemap
- ffmpeg with `librubberband` — formant-preserving pitch shift (`cid_aware`)
- `nvidia-smi` — auto-detect NVENC concurrent-session cap

**Full guide** — prerequisites per OS, AppImage usage, Gatekeeper /
SmartScreen bypass, Docker image, troubleshooting, PyInstaller binary
build: see [`docs/install.md`](./docs/install.md).

## Shipped profiles (16)

**Quality-family** (`src/yt_uniquifier/profiles/`):

| Profile                       | Intent                                                                  |
|-------------------------------|-------------------------------------------------------------------------|
| `soft.yaml`                   | Minimal change, highest quality. Conservative defaults.                 |
| `medium.yaml`                 | Balanced. VMAF ≥ 92 on natural footage.                                 |
| `aggressive.yaml`             | Larger crop / noise / pitch shifts.                                     |
| `medium_hdr.yaml`             | Keep HDR (PQ/HLG) through transforms via zscale wrap.                   |
| `cid_aware.yaml`              | **CID-divergence calibrated** (default for own re-uploads).             |
| `cid_aggressive.yaml`         | Stronger shifts: `video.speed 0.99`, `audio.spectral_smear`, etc.       |
| `cid_aware_hdr_to_sdr.yaml`   | HDR source → SDR output with `cid_aware` transforms.                    |

**Platform-destination** (v0.7.0 — pre-tuned for upload targets):

| Profile                  | Intent                                                                                              |
|--------------------------|-----------------------------------------------------------------------------------------------------|
| `youtube_4k.yaml`        | UHD upload — preserves detail, light CID divergence.                                                |
| `youtube_1080p.yaml`     | Standard 1080p re-upload baseline.                                                                  |
| `youtube_shorts.yaml`    | 9:16 short-form, ≤60 s clamp, mobile-optimised loudness.                                            |
| `tiktok_vertical.yaml`   | 9:16 + TikTok-spec audio loudness + slight motion to avoid duplicate-detect.                        |
| `instagram_reels.yaml`   | 9:16 + Reels loudness target.                                                                       |
| `instagram_square.yaml`  | 1:1 crop + IG-spec loudness.                                                                        |
| `linkedin_square.yaml`   | 1:1 crop + LinkedIn auto-play loudness.                                                             |

Community-contributed profiles via the marketplace —
[`docs/marketplace.md`](./docs/marketplace.md).

## Quickstart

```bash
# 1. Inspect a source.
yt-uniq probe /path/to/master.mp4 | jq '.video[0]'

# 2. Validate against YouTube targets + HDR sanity.
yt-uniq preflight /path/to/master.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml

# 3. (Optional) Index a previous upload so the QA report can warn about
#    self-collisions in Content ID.
yt-uniq corpus add /path/to/old_upload.mp4

# 4. (Optional) Auto-tune intensity for THIS source.
yt-uniq calibrate /path/to/master.mp4 \
  --base src/yt_uniquifier/profiles/cid_aware.yaml \
  --out  /path/to/tuned.yaml \
  --target 0.2

# 5. Re-encode with micro-transforms (resume-capable, parallel CPU).
yt-uniq run /path/to/master.mp4 \
  --profile /path/to/tuned.yaml \
  --out     /path/to/uniq_v1.mp4 \
  --workers 4

# 6. Inspect the QA report (heatmap + SSCD bands + corpus matches).
open /path/to/uniq_v1.mp4.qa.html

# 7. Generate a second, distinct variant.
yt-uniq run /path/to/master.mp4 \
  --profile /path/to/tuned.yaml \
  --out     /path/to/uniq_v2.mp4 \
  --new-variant

# 8. Standalone QA on a pre-existing pair (no encode).
yt-uniq qa /path/to/master.mp4 /path/to/uniq_v1.mp4 --vs-corpus

# 9. Batch a directory on one machine.
yt-uniq batch /path/to/movies/ \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out     /path/to/uniq/

# 10. Distributed batch across N machines (NFSv4 + noac mount).
yt-uniq queue init /shared/queue
yt-uniq queue add  /shared/queue /shared/sources/*.mp4
yt-uniq worker /shared/queue \
  --profile /shared/profiles/cid_aware.yaml \
  --out-dir /shared/uniq/ \
  --workers 4

# 11. Install a community profile from the marketplace.
yt-uniq profile install youtube_shorts_premium

# 12. Launch the desktop GUI.
yt-uniq-gui

# 13. Launch the headless web UI (FastAPI + SSE).
yt-uniq-web --host 0.0.0.0 --port 8000
# or via Docker: docker compose up   (see docs/web.md)
```

## CLI reference

| Command                                                    | What it does                                                          |
|------------------------------------------------------------|-----------------------------------------------------------------------|
| `yt-uniq version`                                          | Print version                                                         |
| `yt-uniq probe <path>`                                     | Print SourceMeta JSON                                                 |
| `yt-uniq probe --encoders`                                 | List working encoders with `max_parallel` cap                         |
| `yt-uniq preflight <in> --profile p.yaml`                  | YouTube target + HDR validation                                       |
| `yt-uniq run <in> --profile p.yaml --out o.mp4 [--workers N] [--new-variant]` | Single-file run with resume + auto QA               |
| `yt-uniq batch <dir> --profile p.yaml --out <dir>`         | Sequential directory processing                                       |
| `yt-uniq qa <in> <out> [--vs-corpus] [--metric sscd]`      | Similarity report + optional corpus / SSCD                            |
| `yt-uniq calibrate <in> --base p.yaml --out tuned.yaml [--metric sscd]` | Bisect intensity to target self-match                    |
| `yt-uniq corpus add/list/remove`                           | Manage local fingerprint corpus                                       |
| `yt-uniq queue init/add/status/reset`                      | Manage a shared-FS distributed queue                                  |
| `yt-uniq worker <queue_dir> --profile p.yaml --out-dir D`  | Long-running queue drainer                                            |
| `yt-uniq profile install/list/uninstall <slug>`            | Marketplace profile management                                        |
| `yt-uniq subtitles <in> [--out s.srt]`                     | Whisper subtitle generation (requires `[ml]` extra)                   |
| `yt-uniq telemetry status/enable/disable/events`           | Local opt-in telemetry control                                        |
| `yt-uniq-gui`                                              | PyQt6 desktop UI (`[gui]` extra)                                      |
| `yt-uniq-web`                                              | Headless FastAPI server (`[web]` extra)                               |

Run any command with `--help` for full flag listings.

## Project docs

📖 **Hosted site**: <https://hostlife22.github.io/yt_uniquifier/> — same content as the `docs/` directory below, rendered with search and dark/light theme via mkdocs-material. Use the hosted site for casual reading; use the in-repo links below when you want to read the version that matches your local checkout.

**Getting started**

- [Install + run guide](./docs/install.md) — pre-built installers, source
  install, GUI launch, Docker, troubleshooting
- [Getting started](./docs/getting-started.md) — first run walkthrough
- [GUI walkthrough](./docs/gui-walkthrough.md) — screen-by-screen tour
- [Web UI & Docker](./docs/web.md) — `yt-uniq-web` + container deploy

**Reference**

- [Architecture](./docs/architecture.md) — layer diagram + module map
- [Profiles](./docs/profiles.md) — YAML schema + transform reference
- [Marketplace](./docs/marketplace.md) — community profile catalog
- [Plugins](./docs/plugins.md) — third-party transform packages
- [Filter graph](./docs/filter_graph.md) — how transforms compose
- [Seed strategy](./docs/seed_strategy.md) — `per_run` / `per_file` /
  `fixed` / `divergent`
- [YouTube targets](./docs/youtube_targets.md) — preflight matrix

**Operations**

- [Calibrate workflow](./docs/calibrate.md) — `yt-uniq calibrate`
- [QA report fields](./docs/qa_report.md) — `.qa.json` / `.qa.html` schema
- [SSCD QA](./docs/sscd.md) — semantic-similarity model option
- [Corpus index](./docs/corpus.md) — fingerprint database
- [Distributed batch](./docs/distributed.md) — shared-FS workflow
- [Validation harness](./docs/validation_harness.md) — real-CID
  upload-observe-record loop
- [Telemetry](./docs/telemetry.md) — opt-in local event log
- [Localization](./docs/i18n.md) — `QTranslator` + locale catalog

**Project**

- [Versioning & SemVer](./docs/versioning.md) — stable contract surface
- [API contracts](./docs/api-contracts.md) — frozen field-by-field reference
- [Accessibility](./docs/accessibility.md) — WCAG 2.1 AA conformance
- [Security policy](./SECURITY.md) — private disclosure + CVSS table
- [Contributing](./CONTRIBUTING.md) — dev loop + RFC process
- [Changelog](./CHANGELOG.md) — release notes per tag

## Status

<!-- AUTO-GENERATED: regenerated from git tags; see CHANGELOG.md for details. -->

- **v0.1.0** — foundation pipeline, single-host single-file flow ✅
- **v0.2.0** — CID-divergence calibration, corpus, calibrate loop ✅
- **v0.3.x** — HDR→SDR tonemap, parallel GPU detect, distributed batch,
  audio CID resistance (rubberband, loudnorm jitter, compand, reverb),
  Smitelli pitch threshold fix, Haas stereo, temporal jitter,
  divergent per-segment seeds, parametric noise overlay ✅
- **v0.4.x** — Poisson temporal_jitter, subpixel_sharpen, `encoder=`
  strip, real-CID validation harness, per-segment audio divergence
  with `acrossfade` seams, `--sanitize-bitstream` libx264 pass ✅
- **v0.5.x** — PyQt6 desktop foundation (10 screens, `WorkerBase`
  contract), QA Viewer, Profile Editor, History, Queue dashboard,
  3-step Validation wizard, Settings + Corpus, PyInstaller packaging ✅
- **v0.6.0** — performance baseline + signed installer scaffolding ✅
- **v0.7.0** — GUI maturity (divergence sparkline, pause/resume,
  auto-tune), 7 platform-destination profiles, post-job webhooks
  (Discord/Slack/Telegram/email), full keyboard navigation + theme
  contrast pass ✅
- **v0.8.0** — third-party transform plugins (entry-points + trust
  model), SSCD semantic-similarity QA, content-aware scene-cut
  segmentation, per-segment VMAF target with bounded retry,
  Whisper subtitle injection ✅
- **v0.9.0** — community profile marketplace (HTTPS + SHA-pinned),
  FastAPI web UI + Docker image, opt-in local telemetry, English +
  Russian UI (`QTranslator`), mkdocs-material documentation site ✅
- **v1.0.0** — frozen API + SemVer contract, 41-file contract snapshot
  suite, 80%+ core coverage gate, nightly perf-regression CI,
  Linux AppImage installer + SHA256SUMS, WCAG 2.1 AA conformance,
  SECURITY.md disclosure policy, RFC process via issue templates ✅

`ruff` + `mypy --strict` clean. CI runs on Ubuntu + macOS + Windows for
Python 3.11 / 3.12. Coverage gate `--cov-fail-under=80` on `core/`.

## Development

```bash
make dev                          # .venv + pip install -e ".[dev,gui]"
make check                        # ruff + mypy --strict + full pytest
make test                         # pytest only
make build                        # PyInstaller bundle
```

Performance benchmarks + regression tracking under `tools/`:

```bash
python tools/benchmark.py /path/to/movie.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out /tmp/uniq.mp4 --encoder libx264 --workers 4 \
  --json /tmp/bench.json

python tools/perf_compare.py \
  --baseline perf-history/baseline.json \
  --current  /tmp/bench.json \
  --threshold 15
```

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the full dev workflow,
commit conventions, and RFC process for stable-contract changes.

## License

MIT — see [LICENSE](./LICENSE).
