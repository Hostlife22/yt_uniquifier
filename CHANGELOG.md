# Changelog

All notable changes per tagged release. Format: keep-a-changelog style;
versioning follows the git tags `v0.1.0`, `v0.2.0`, `v0.3.x`.

The `[Unreleased]` section, if present, summarises post-tip changes since
the last tag.

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

[v0.3.3]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.3
[v0.3.2]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.2
[v0.3.1]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.1
[v0.3.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.3.0
[v0.2.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.2.0
[v0.1.0]: https://github.com/Hostlife22/yt_uniquifier/releases/tag/v0.1.0
