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
target_codec: "h264" | "hevc"
target_loudness_lufs: float                                   # default -14.0
seed: int | null                                              # used when seed_strategy=fixed
seed_strategy: "fixed" | "per_run" | "per_file" | "divergent" # default per_run; v0.3.3 CID profiles ship divergent
```

Unknown top-level fields are rejected (`extra=forbid`).

See [seed_strategy.md](./seed_strategy.md) for what each strategy means
and when to pick which.

## Shipped profiles

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

\* indicative on natural footage; synthetic test patterns score much lower.

## Transform reference (18)

| ID | Kind | Notable params |
|----|------|----------------|
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
