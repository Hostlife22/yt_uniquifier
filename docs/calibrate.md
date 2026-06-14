# Calibrate workflow

`yt-uniq calibrate` finds a profile intensity that drops the predicted
Content-ID self-match below a target, **without** crashing perceptual
quality below a floor. It encodes only a short test clip per iteration,
so calibration of a 2-hour film takes minutes, not hours.

The output is a new YAML profile you then feed to `yt-uniq run` against
the full source.

## TL;DR

```bash
yt-uniq calibrate /path/to/master.mp4 \
  --base src/yt_uniquifier/profiles/cid_aware.yaml \
  --out  /path/to/tuned.yaml \
  --target 0.2          # max predicted self-match (default)
  --min-quality 88.0    # min quality score on the unified scale (default)
  --iterations 5        # max bisect steps (default)
  --clip-sec 60.0       # test clip duration in seconds (default)
  --metric phash        # v0.8 R6: 'phash' (default) | 'sscd' (semantic-similarity, requires [ml] extra)

yt-uniq run /path/to/master.mp4 \
  --profile /path/to/tuned.yaml \
  --out     /path/to/uniq.mp4
```

## What it does

1. Cuts the first `--clip-sec` of the source via stream-copy (no re-encode).
2. Runs a real `orchestrator.run_full` on the clip with the current
   profile.
3. Measures `cid_predict_self` and the **quality score** (see below) on
   the resulting variant.
4. Bisects an `intensity_factor` between 0.1 and 5.0:
   - if `self_match > target` → factor × 1.5 (push harder)
   - elif `quality < min_quality` → factor / 1.3 (back off)
   - else → converged
5. Tracks the best step seen so far (lowest `self_match` among steps
   whose quality passed, or just lowest `self_match` if none did).
6. Applies the winning factor to every transform via
   `core.calibration.intensity.scale_profile`, which multiplicatively
   scales the parameters that have an obvious "intensity" knob
   (`max_strength`, `degrees`, `brightness` deltas, `strength`, `noise_db`,
   `blackout_prob`, …) around their identity points, clamped to each
   transform's pydantic schema bounds.
7. Writes the resulting profile to `--out`.

## The quality fallback chain

VMAF is the canonical perceptual quality metric, but it requires ffmpeg
built with libvmaf — not every install has it. When it's missing, the
calibration loop **falls back** through these metrics, all rescaled onto
a unified 0..100 scale so `--min-quality 88` means the same thing
everywhere:

| Priority | Metric | Source | Unified scale |
|---|---|---|---|
| 1 | VMAF | `libvmaf` filter | 0..100 verbatim |
| 2 | SSIM × 100 | `ssim` filter | `ssim_mean * 100` |
| 3 | pHash similarity × 100 | imagehash on 120 frames | `phash_similarity * 100` |

`core.qa.quality.quality_score` picks the first available metric and
returns a `QualityScore(score, metric)`. The HTML QA report shows which
one was used so the calibration log is auditable.

This was a fix in v0.3.1 — earlier calibration silently treated
"VMAF missing" as "quality is null" and never backed off, producing
over-aggressive profiles on machines without libvmaf.

## Tuning the knobs

| Flag | Default | When to change |
|---|---|---|
| `--target` | 0.2 | Lower (e.g. 0.1) for stricter divergence; higher (0.3) for less aggressive |
| `--min-quality` | 88.0 | Lower (e.g. 82) on quality-tolerant content like B-roll; never below 75 (visibly degraded) |
| `--iterations` | 5 | Bump to 8 if calibration logs say "didn't converge — best-so-far at iteration N" |
| `--clip-sec` | 60.0 | Bump to 120s for content with high scene variance; shorter for very stable footage |
| `--encoder` | auto | Pin to `libx264` if you need calibration to match later batch runs that use libx264; otherwise let it pick |

## When calibration "fails"

You'll see one of:

```
warning: did not converge after 5 iterations.
         using best-so-far: factor=2.25, self_match=0.41
```

This means no step hit both the self-match target AND the quality floor.
Two common causes:

1. **Source is unusually CID-stable** — same-looking talking-head, no
   scene changes, stationary camera. The default transforms can't move
   pHash low enough without crushing VMAF.
   - Try `--base cid_aggressive.yaml` for stronger starting transforms.
   - Try `--min-quality 82` to allow more visible degradation.

2. **Source is unusually quality-fragile** — text overlays, sharp lines,
   colour gradients. Even small color jitter crashes VMAF.
   - Disable `video.color_eq` or lower its delta in the base profile.
   - Switch to `--base medium.yaml` (no CID-aware shifts).

## What it doesn't do

Calibration tunes **multiplicative intensity** on parameters that have a
natural "more / less" axis. It does **not**:

- Toggle transforms on/off (use the YAML for that).
- Pick which `seed_strategy` to use (orthogonal — see [seed_strategy.md](./seed_strategy.md)).
- Re-order transforms (pipeline is fixed at "video first → audio first" within each kind).
- Calibrate against a **real** Content ID API. There is no public CID API.
  The target is `cid_predict_self`, our heuristic predictor — the only
  authoritative test is uploading a variant to an unlisted YouTube channel.

## Programmatic API

```python
from pathlib import Path
from yt_uniquifier.core.calibration.loop import calibrate, CalibrationTarget
from yt_uniquifier.core.profile_loader import load_profile, dump_profile

base = load_profile(Path("profiles/cid_aware.yaml"))
result = calibrate(
    input_path=Path("/movies/master.mp4"),
    base_profile=base,
    target=CalibrationTarget(
        max_self_match=0.15,
        min_quality=86.0,
        max_iterations=8,
        test_clip_sec=90.0,
    ),
    work_dir=Path("/tmp/calib"),
)

if result.converged:
    print(f"converged at factor={result.factor:.2f}")
else:
    print(f"best-so-far: factor={result.factor:.2f}, self_match={result.final_self_match:.3f}")

dump_profile(result.profile, Path("tuned.yaml"))
```

`CalibratedResult.steps[]` carries every intermediate iteration's profile,
self-match, quality, and the metric that was used — useful when piping
into a UI progress display or just for debugging.

## Integration with corpus

If you've previously uploaded variants and indexed them via
`yt-uniq corpus add`, the QA reports during calibration also flag
`corpus_matches`. Calibration itself bisects only on `cid_predict_self`
(self-match against the source), not against the corpus. To screen for
collisions across past variants, run `yt-uniq qa <input> <output> --vs-corpus`
on the calibrated test clip before applying the tuned profile to the full
source.
