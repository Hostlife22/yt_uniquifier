# Profiles

A profile is a YAML file declaring which transforms apply and at what intensity.
Profiles live under `src/yt_uniquifier/profiles/` and can be loaded by path.

## YAML schema

```yaml
name: string                                                  # required
description: string                                           # optional
transforms:                                                   # list
  - id: <transform.id>                                        # see Transform reference
    enabled: bool                                             # default true
    params: { ... }                                           # validated by the transform's schema
audio_tracks: "first" | "all" | [int, ...]
keep_hdr: bool                                                # default false
output_container: "mp4" | "mov"                               # default mp4
target_codec: "h264" | "hevc" | "av1"          # v1.2.0 Task 22 added av1
target_loudness_lufs: float                                   # default -14.0
seed: int | null                                              # used when seed_strategy=fixed
seed_strategy: "fixed" | "per_run" | "per_file" | "divergent" # default per_run; v0.3.3 CID profiles ship divergent

# --- v0.8.0 R3 — segmentation strategy ---
segmentation:
  mode: "keyframe" | "scene"                                  # default keyframe
  scene_threshold: float                                       # default 27.0 (PySceneDetect ContentDetector)
  scene_min_length_sec: float                                  # default 2.0
# --- v0.8.0 R5 — per-segment VMAF target-quality ---
target_vmaf: float | null                                      # default null (loop disabled)
target_vmaf_step: int                                          # default 2 (CRF decrement per retry)
target_vmaf_max_retries: int                                   # default 2 (cap on re-encode attempts)
```

Unknown top-level fields are rejected (`extra=forbid`).

### Segmentation modes (v0.8.0 R3)

* **`keyframe`** (default) — segments aligned on source keyframes,
  enabling stream-copy extraction. This is the v0.7 behaviour and what
  every shipped profile uses.
* **`scene`** — boundaries detected by [PySceneDetect's ContentDetector](https://github.com/Breakingofstars/PySceneDetect),
  then snapped DOWN to the nearest keyframe to preserve the stream-copy
  invariant. Falls back to a single segment if no scene cuts survive
  the snap. Requires the `[scene]` extra
  (`pip install 'yt-uniquifier[scene]'`).

### Per-segment VMAF target-quality (v0.8.0 R5)

When `target_vmaf` is set, each segment is re-encoded with progressively
lower CRF (decrement = `target_vmaf_step`, cap = `target_vmaf_max_retries`)
until VMAF clears the target or retries are exhausted. The default CRF
hint is 18 (x264/x265) — hardware encoders preserve the delta and map to
their native quality knob (nvenc `cq`, qsv `global_quality`, amf `qp`).

**Distributed-mode warning**: the feedback loop is single-host only. A
profile shipped to a `yt-uniq worker` queue logs a yellow warning and
strips `target_vmaf` before running, to avoid invalidating per-segment
lease accounting.

See [seed_strategy.md](./seed_strategy.md) for what each strategy means
and when to pick which.

## Shipped profiles

### Quality / divergence family

| Name | Aim | VMAF target* |
|------|-----|--------------|
| `soft.yaml`                 | minimal change, highest quality                                        | ~96 |
| `medium.yaml`               | balanced (v0.1 default recommendation)                                  | ~92 |
| `aggressive.yaml`           | strong fingerprint shifts                                              | ~85 |
| `legacy_ab.yaml`            | frame-blend with a B-video (port of legacy AB)                          | varies |
| `medium_hdr.yaml`           | medium transforms with HDR (PQ/HLG) preserved via zscale linear wrap    | ~90 |
| `cid_aware.yaml`            | **v0.3.3 default for CID divergence on own content** — past Smitelli ±5 % pitch zone, temporal jitter, divergent per-segment seeds, Haas widener | ~85 |
| `cid_aggressive.yaml`       | cid_aware + stronger shifts + parametric noise overlay + reverb        | ~80 |
| `cid_aware_hdr_to_sdr.yaml` | HDR source → SDR output, cid_aware audio/video transforms              | ~80 |

### Platform-destination family (v0.7.0)

Aspect-locked presets for direct upload to specific social platforms.
Each one chains `video.fit_aspect` (target geometry + fit mode) with
mild quality-preserving transforms and a platform-appropriate
`audio.loudnorm` integrated target. Drop-in for cross-posting workflows
where the source is landscape but the destination expects vertical /
square frames — no manual ffmpeg recipe needed.

| Name | Target | Mode | Resolution | LUFS | Notes |
|------|--------|------|------------|------|-------|
| `youtube_4k.yaml`        | 16:9 | `crop`     | 3840×2160 | -14 | YouTube 4K landscape master |
| `youtube_1080p.yaml`     | 16:9 | `crop`     | 1920×1080 | -14 | YouTube standard upload |
| `youtube_shorts.yaml`    | 9:16 | `crop`     | 1080×1920 | -14 | YouTube Shorts (centre-crop) |
| `tiktok_vertical.yaml`   | 9:16 | `crop`     | 1080×1920 | -16 | TikTok loudness target stricter than YT |
| `instagram_reels.yaml`   | 9:16 | `pad_blur` | 1080×1920 | -14 | Letterbox-blur background preserves full landscape frame |
| `instagram_square.yaml`  | 1:1  | `pad_blur` | 1080×1080 | -14 | Square feed post with blurred sidebars |
| `linkedin_square.yaml`   | 1:1  | `crop`     | 1080×1080 | -14 | LinkedIn timeline (no blur — professional look) |

### AV1 family (v1.2.0 Task 22)

AV1 yields ~30 % smaller files than H.264 at equivalent VMAF and is
YouTube's preferred ingest codec for 2024+. Profiles target `av1` —
`pick_encoder()` chooses, in order: `av1_vulkan` (cross-vendor, FFmpeg
8.0+), `av1_nvenc`/`av1_qsv`/`av1_amf`/`av1_videotoolbox` (hardware),
`libsvtav1` (CPU, ~3× libx264 wall-clock), `libaom-av1` (CPU,
reference, ~10× libx264). The CRF scale is 0..63 with default 30
(≈ libx264 CRF 18 quality); `target_vmaf` retries map onto the same
scale automatically.

| Name | Target | Mode | Resolution | LUFS | Notes |
|------|--------|------|------------|------|-------|
| `youtube_av1.yaml`       | 16:9 | `crop`     | 1920×1080 | -14 | YouTube AV1 1080p — prefer hardware AV1 when available |
| `youtube_4k_av1.yaml`    | 16:9 | `crop`     | 3840×2160 | -14 | YouTube AV1 4K — uncaps perceived quality vs H.264's 25 Mb/s ceiling |

\* indicative on natural footage; synthetic test patterns score much lower.

⚠️ HDR + `pad_blur`: `gblur` is not HDR-aware. If the source is HDR
(PQ/HLG) you must either tonemap to SDR first (chain after
`video.tonemap_sdr`) or pick a `crop` / `pad_black` variant. Preflight
flags this combination as `FAIL` so the run aborts before wasted
encode time.

## Transform reference (19)

| ID | Kind | Notable params |
|----|------|----------------|
| `video.fit_aspect`      | video | `target_aspect` (`16:9` / `9:16` / `1:1` / `4:5` / `4:3`), `mode` (`crop` / `pad_blur` / `pad_black`), `target_width`, `target_height`, `blur_sigma` (0..80, only `pad_blur`), `pad_color` (only `pad_black`) — v0.7 R3 / F3, used by all platform-destination profiles |
| `video.crop_resize`     | video | `max_strength` (0..0.10), `rng_seed` |
| `video.rotate`          | video | `degrees` (-2..2), `fillcolor_sdr`, `fillcolor_pq` (HDR variant) |
| `video.color_eq`        | video | `brightness`, `contrast`, `gamma`, `saturation` |
| `video.noise`           | video | `strength` (0..100) |
| `video.mirror`          | video | (no params) — horizontal flip; opt-in only |
| `video.blend_b`         | video | `b_video_path`, `opacity` (0.01..0.15) |
| `video.speed`           | video | `rate` (0.5..2.0) |
| `video.temporal_jitter` | video | `blackout_prob` (0..0.2), `drop_prob` (0..0.2), `blackout_blur` (bool) |
| `video.tonemap_sdr`     | video | `tone` (hable / reinhard / mobius / aces), `peak_nits`, `desat` |
| `audio.pitch_tempo`     | audio | `pitch`, `tempo`, `sample_rate`, `method` (rubberband / asetrate), `randomize_within` |
| `audio.eq`              | audio | `bands` (list of `(freq_hz, gain_db)`), `width_q`, `randomize_bands` |
| `audio.resample`        | audio | `intermediate_sr` (e.g. 47999) |
| `audio.compand`         | audio | `threshold_db`, `ratio`, `randomize_within` |
| `audio.reverb`          | audio | `intensity` (0..0.5), `style` (small_room / medium_room / hall / plate) |
| `audio.spectral_smear`  | audio | `intensity` (0..0.10), `delay_ms`, `speed` |
| `audio.haas_stereo`     | audio | `delay_ms` (1..40), `randomize_within_ms` |
| `audio.noise_overlay`   | audio | `noise_db` (-40..-3), `color` (white / pink / brown), `randomize_within_db` |
| `audio.loudnorm`        | audio | `integrated` (LUFS), `true_peak`, `lra`, `target_jitter_lufs` |

## Writing your own profile

1. Copy `cid_aware.yaml` (for CID-divergence use cases) or `medium.yaml`
   (for general quality-first re-encoding) to `my_profile.yaml`.
2. Toggle `enabled` and tune `params`.
3. Run with `yt-uniq run --profile my_profile.yaml …`.
4. Inspect `<output>.qa.html` — adjust intensities until you hit the band
   `pHash similarity ∈ (0.55, 0.85]` (for CID divergence) or `(0.85, 0.97]`
   (for quality-first) and `VMAF ≥ 85` (on natural footage).
5. For automatic intensity tuning, use `yt-uniq calibrate` — see
   [calibrate.md](./calibrate.md).

## Notes

- The `seed` field is used by `video.crop_resize.rng_seed` when set; same seed
  → identical filter graph, useful for reproducibility.
- `audio.loudnorm` is a **two-pass** filter — the first pass measures the
  full source and the result is cached in `state.json`, so resume runs skip it.
- `video.blend_b` requires a B-video path. Pass it via `--b-video <path>` on
  the CLI; the value is injected into the transform's params at runtime.

## Performance notes

- **`audio.pitch_tempo` with `method: rubberband`** (used by `cid_aware`
  and `cid_aggressive`) is ~5–10× slower than the default
  `asetrate+atempo` path. Rubberband preserves formants — important for
  voice content — but on long clips the audio chain can run 10–20×
  realtime instead of <1× realtime. The 2026-05-31 sweep measured a
  90 s clip × `cid_aware` × `libx264` at ~18 minutes wall on an
  8-core Mac. For batch throughput on non-voice content, prefer
  `medium` / `aggressive` (which omit the `method: rubberband` flag and
  fall back to the atempo path).
- **Preflight requires `rubberband` and `zscale` filters** when the
  profile uses them. The dry-run probe at run start fails fast (<1 s)
  if ffmpeg lacks `librubberband` (used by rubberband pitch path) or
  `libzimg` (used by `video.tonemap_sdr` and `keep_hdr: true`).
  Homebrew's default ffmpeg ships neither — install with
  `brew install ffmpeg --HEAD` or build from source with
  `--enable-librubberband --enable-libzimg`.

### Rubberband performance characteristic

The 2026-05-31 real-video matrix (`docs/bug-triage-2026-05-31.md` §9)
measured wall time across the shipped profile × input combinations on
`evermeet.cx` ffmpeg 8.1.1 + `librubberband` + `libzimg` + `libvmaf`,
single-worker, `libx264`. Numbers are wall seconds:

| Profile         | clip_a 30 s | clip_long 90 s | synth_sdr_4k 12 s | synth_long_5min 300 s |
|---              |---:         |---:            |---:               |---:                   |
| `soft`          | 17          | 37             | —                 | 136 (run) + 326 (resume) |
| `medium`        | 17          | 39             | —                 | 145                   |
| `aggressive`    | 20          | 46             | —                 | 242                   |
| `cid_aware`     | 224         | 623            | TIMEOUT (>1800)   | TIMEOUT (>1800)       |
| `cid_aggressive`| 316         | 871            | TIMEOUT (>1800)   | TIMEOUT (>1800)       |

`cid_aware` / `cid_aggressive` run ~10–15× the wall time of
`soft` / `medium` / `aggressive` on the same content. The rubberband
audio chain is single-threaded inside ffmpeg and runs serially with
the (parallel) video chain, so on `>60 s` or `>1080p` sources the
audio pass dominates wall time. Choice is intentional:

- **Keep `rubberband`** when formant preservation matters more than
  throughput — voice content, podcasts, talking-head video, anything
  where the "chipmunk effect" of `asetrate` would be unacceptable.
  Smitelli 2010 places the CID match boundary at ±5% pitch, so the
  pitch shift in `cid_*` profiles is intentionally past that threshold.
- **Switch to `asetrate`** when throughput matters more — B-roll,
  music-only content, batch jobs of large files. Edit the profile YAML
  in place or save a derived copy:

```yaml
# my_fast_cid.yaml — cid_aware with the asetrate fallback
transforms:
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.06, randomize_within: 0.005, method: asetrate}
```

`preflight()` emits `audio.pitch.rubberband.slow` (severity=`warn`)
when a rubberband-enabled profile runs on a source `>60 s` or
`>1080p` so the wall-cost is surfaced before the encode starts. The
WARN is informational — encode still proceeds. Implemented in
`src/yt_uniquifier/core/preflight.py::_check_rubberband_perf`.

_(Measured 2026-05-31 on `evermeet.cx` ffmpeg 8.1.1 + librubberband
on an 8-core Mac. Re-measure annually or after an ffmpeg major bump.)_

## Why these defaults? — Smitelli citation

The `cid_aware` and `cid_aggressive` profiles target YouTube Content ID
audio matching thresholds documented in Scott Smitelli's 2010 controlled
experiment ("Fun with YouTube's Audio Content ID System",
<https://www.scottsmitelli.com/articles/youtube-audio-content-id>).

Verified historical thresholds:

| Transform | CID matches | CID does not match |
|---|---|---|
| pitch shift | within ±5 % (1.04–1.05 was inside match zone) | ≥ ±6 % |
| white-noise overlay | mix < 45 % | mix ≥ 45 % |
| stereo phase | identity | full inversion |

**v0.3.2 defaults that follow from these thresholds:**

- `cid_aware.audio.pitch_tempo.pitch = 1.06` — just past the documented
  +5 % match boundary; `randomize_within: 0.005` keeps the lower bound at
  1.055 (still on the no-match side).
- `cid_aggressive.audio.pitch_tempo.pitch = 1.08` — comfortable margin.
- `audio.haas_stereo` with `delay_ms ≈ 15 ms` (cid_aware) or
  `delay_ms ≈ 25 ms` (cid_aggressive) — mono-compatible variant of stereo
  phase inversion; shifts cross-channel phase without the audible artefact
  of true inversion.

These are not guarantees, only verified historical thresholds. YouTube's
CID has been updated since 2010; community reports suggest the thresholds
sit in roughly the same ranges, but the only authoritative test is an
upload against your own corpus.
