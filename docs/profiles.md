# Profiles

A profile is a YAML file declaring which transforms apply and at what intensity.
Profiles live under `src/yt_uniquifier/profiles/` and can be loaded by path.

## YAML schema

```yaml
name: string                       # required
description: string                # optional
transforms:                        # list
  - id: <transform.id>             # see "Transform reference" below
    enabled: bool                  # default true
    params: { ... }                # validated by the transform's schema
audio_tracks: "first" | "all" | [int, ...]
keep_hdr: bool                     # default false
output_container: "mp4" | "mov"    # default mp4
target_codec: "h264" | "hevc"
target_loudness_lufs: float        # default -14.0
seed: int | null                   # used by transforms that randomize
```

Unknown top-level fields are rejected (`extra=forbid`).

## Shipped profiles

| Name | Aim | VMAF target* |
|------|-----|--------------|
| `soft.yaml`        | minimal change, highest quality | ~96 |
| `medium.yaml`      | balanced (default recommendation) | ~92 |
| `aggressive.yaml`  | strong fingerprint shifts | ~85 |
| `legacy_ab.yaml`   | frame-blend with a B-video (port of legacy AB) | varies |

\* indicative on natural footage; synthetic test patterns score much lower.

## Transform reference

| ID | Kind | Notable params |
|----|------|----------------|
| `video.crop_resize` | video | `max_strength` (0..0.10), `rng_seed` |
| `video.rotate`      | video | `degrees` (-2..2) |
| `video.color_eq`    | video | `brightness`, `contrast`, `gamma`, `saturation` |
| `video.noise`       | video | `strength` (0..100) |
| `video.blend_b`     | video | `b_video_path`, `opacity` (0.01..0.15) |
| `video.speed`       | video | `rate` (0.5..2.0) |
| `audio.pitch_tempo` | audio | `pitch`, `tempo`, `sample_rate` |
| `audio.eq`          | audio | `bands` (list of `(freq_hz, gain_db)`), `width_q` |
| `audio.loudnorm`    | audio | `integrated` (LUFS), `true_peak`, `lra` |

## Writing your own profile

1. Copy `medium.yaml` to `my_profile.yaml`.
2. Toggle `enabled` and tune `params`.
3. Run with `yt-uniq run --profile my_profile.yaml …`.
4. Inspect `<output>.qa.html` — adjust intensities until you hit the band
   `pHash similarity ∈ (0.85, 0.97]` and `VMAF ≥ 90` (on natural footage).

## Notes

- The `seed` field is used by `video.crop_resize.rng_seed` when set; same seed
  → identical filter graph, useful for reproducibility.
- `audio.loudnorm` is a **two-pass** filter — the first pass measures the
  full source and the result is cached in `state.json`, so resume runs skip it.
- `video.blend_b` requires a B-video path. Pass it via `--b-video <path>` on
  the CLI; the value is injected into the transform's params at runtime.
