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
audio_tracks: "first" | "all" | [int, ...]                  # list = absolute ffprobe stream indices
keep_hdr: bool                                                # default false
output_container: "mp4" | "mov" | "mkv"                       # default mp4
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

`audio_tracks: first` selects the first probed audio stream. `all` preserves every
audio stream in source order. An explicit list contains absolute stream indices from
`ffprobe`/`SourceMeta.audio[].index`; the output follows the list order and invalid
indices fail before encoding. The first selected stream is the one processed by audio
transforms; remaining streams are copied when the target container supports their
codec and otherwise transcoded according to the mux policy.

`video.crop_resize.max_strength` is the maximum **total** fraction removed on each
axis. For example, `0.06` removes at most 6% of width in total across left+right, not
6% independently on each side. Output SAR is reset to 1:1 after rescaling.

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
The highest-scoring encoded candidate is retained when retries are exhausted.

Distributed workers preserve `target_vmaf` and execute the same bounded per-segment
loop. The unregistered-reference guard still applies before encoding.

See [seed_strategy.md](./seed_strategy.md) for what each strategy means
and when to pick which.

## Shipped profiles

### Quality / high-change family

| Name | Aim | Validation status |
|------|-----|-------------------|
| `soft.yaml`                 | conservative authorized derivative                               | synthetic correctness passed; natural quality band pending |
| `medium.yaml`               | moderate processing                                               | natural quality band pending |
| `aggressive.yaml`           | experimental visible/audible processing                           | mandatory operator review |
| `legacy_ab.yaml`            | frame blend with a licensed B-video                               | experimental |
| `medium_hdr.yaml`           | preserve PQ/HLG through a float-RGB linear-light transform wrapper | synthetic and derived natural-scene HDR passed; native-camera HDR pending |
| `cid_aware.yaml`            | legacy experimental high-change saved-job compatibility           | no external-system prediction |
| `cid_aggressive.yaml`       | legacy maximum-change compatibility                               | not quality-first; mandatory review |
| `cid_aware_hdr_to_sdr.yaml` | experimental HDR→SDR derivative                                   | synthetic tonemap passed; natural HDR pending |

### Platform-destination family (v0.7.0)

Aspect-locked presets for direct upload to specific social platforms.
Each one chains `video.fit_aspect` (target geometry + fit mode) with
mild quality-preserving transforms and a platform-appropriate
`audio.loudnorm` integrated target. Drop-in for cross-posting workflows
where the source is landscape but the destination expects vertical /
square frames — no manual ffmpeg recipe needed.

The shipped destination profiles explicitly set `allow_upscale: true` because
their names promise the resolution in the table. A custom `video.fit_aspect`
transform defaults to `allow_upscale: false`: crop chooses the largest even
target-aspect rectangle inside the source and configured caps, while pad modes
may enlarge the canvas but never enlarge the source foreground. Set the flag to
`true` only when a fixed delivery canvas is an explicit requirement; upscaling
cannot restore source detail.

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

AV1 is available as an optional delivery codec. Actual size, quality and encode
time depend on the encoder and source and must be benchmarked; no fixed percentage
or platform preference is assumed. Profiles target `av1` —
`pick_encoder()` defaults to the `quality` policy: `libaom-av1`, then
`libsvtav1`, then a verified hardware encoder. `balanced` prefers SVT-AV1 and
`speed` prefers verified NVENC/QSV/VideoToolbox/AMF; configure them with
`YT_UNIQ_ENCODER_POLICY`. `av1_vulkan` is not advertised because the existing
CPU-frame filter graph has no validated Vulkan `hwupload` path. The CRF scale is
0..63 with default 30; quality and speed are not numerically equivalent to an
x264 CRF and require a content-specific control benchmark.

| Name | Target | Mode | Resolution | LUFS | Notes |
|------|--------|------|------------|------|-------|
| `youtube_av1.yaml`       | 16:9 | `crop`     | 1920×1080 | -14 | AV1 1080p; benchmark selected encoder against control |
| `youtube_4k_av1.yaml`    | 16:9 | `crop`     | 3840×2160 | -14 | AV1 4K; explicit canvas may upscale smaller sources |

The project does not publish profile VMAF bands until a licensed natural-content
corpus has been run with temporally and spatially registered references.

⚠️ HDR + `pad_blur`: `gblur` is not HDR-aware. If the source is HDR
(PQ/HLG) you must either tonemap to SDR first (chain after
`video.tonemap_sdr`) or pick a `crop` / `pad_black` variant. Preflight
flags this combination as `FAIL` so the run aborts before wasted
encode time.

## Transform reference (19)

| ID | Kind | Notable params |
|----|------|----------------|
| `video.fit_aspect`      | video | `target_aspect` (`16:9` / `9:16` / `1:1` / `4:5` / `4:3`), `mode` (`crop` / `pad_blur` / `pad_black`), `target_width`, `target_height`, `allow_upscale` (default `false`), `blur_sigma` (0..80, only `pad_blur`), `pad_color` (only `pad_black`) — used by all platform-destination profiles |
| `video.crop_resize`     | video | `max_strength` (0..0.10), `rng_seed` |
| `video.rotate`          | video | `degrees` (-2..2), `fillcolor_sdr`, `fillcolor_pq` (HDR variant) |
| `video.color_eq`        | video | `brightness`, `contrast`, `gamma`, `saturation` |
| `video.noise`           | video | `strength` (0..100) |
| `video.mirror`          | video | (no params) — horizontal flip; opt-in only |
| `video.blend_b`         | video | `b_video_path`, `opacity` (0.01..0.15) |
| `video.speed`           | video | `rate` (0.5..2.0) |
| `video.temporal_jitter` | video | Experimental/destructive, opt-in only: `blackout_prob` (0..0.2), `drop_prob` (0..0.2), `blackout_blur` (bool); deterministic 60 s pattern uses a 24 Hz PTS grid, independent of source FPS |
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

1. Copy `soft.yaml` or `medium.yaml` to `my_profile.yaml`. Start from the
   smallest transform set that meets the authorized derivative's editorial need.
2. Toggle `enabled` and tune `params`.
3. Run with `yt-uniq run --profile my_profile.yaml …`.
4. Inspect `<output>.qa.html`. Correctness must pass before interpreting quality
   or internal similarity diagnostics. Do not apply a VMAF threshold until the
   source/output pair has valid temporal and spatial registration.
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

- **`audio.pitch_tempo` with `method: rubberband`** (used by legacy `cid_aware`
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
- **Do not select a pitch amount from an external matching threshold.** Use only
  an editorially justified amount and verify speech/music quality by listening.
- **Switch to `asetrate`** when throughput matters more — B-roll,
  music-only content, batch jobs of large files. Edit the profile YAML
  in place or save a derived copy:

```yaml
# my_fast_derivative.yaml — legacy profile with the asetrate fallback
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

## Legacy high-change profiles

`cid_aware` and `cid_aggressive` keep their identifiers so existing saved jobs and
automation continue to load. They are experimental effect stacks, not production
quality defaults. Their pitch, temporal, phase, dynamics and noise operations can
be visible or audible and require review on owned or licensed content.

pHash, audio similarity and SSCD results are used only for regression diagnostics
and self-collision analysis. They do not predict or guarantee the behavior of
YouTube Content ID or another external rights-management system.

When `target_vmaf` is combined with geometry, retiming, mirroring, overlays,
subtitles or tonemapping, preflight now fails with
`quality.target_vmaf.unregistered_reference`. The current feedback loop can tune
encoder CRF only; it cannot interpret an unregistered source/output comparison as
compression quality. Run registered post-processing QA instead.
