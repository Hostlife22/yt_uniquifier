# Changelog

<!-- AUTO-GENERATED: sections below v0.3.3 are regenerated from git tags;
     manual prose may be appended within each version block. -->

All notable changes per tagged release. Format: keep-a-changelog style;
versioning follows the git tags `v0.1.0`, `v0.2.0`, `v0.3.x`, `v0.4.x`,
`v0.5.x`.

The `[Unreleased]` section, if present, summarises post-tip changes since
the last tag.

## [Unreleased]

Post-v0.5.4 hardening — robustness, concurrency, and lifecycle fixes
surfaced by two rounds of internal audit. No new user-facing features;
all changes additive or behaviour-preserving.

### Fixed

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
