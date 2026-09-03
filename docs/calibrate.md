# Experimental calibration workflow

`yt-uniq calibrate` explores profile intensity against the project's internal
self-similarity heuristic and a provisional quality constraint. It does not predict
or optimize the behavior of YouTube Content ID or another external rights system.
The command remains experimental because one short clip is not representative and
its current VMAF/SSIM/pHash fallback scales are not mathematically interchangeable.

The output is a new YAML profile you then feed to `yt-uniq run` against
the full source.

## TL;DR

```bash
yt-uniq calibrate /path/to/master.mp4 \
  --base src/yt_uniquifier/profiles/medium.yaml \
  --out  /path/to/tuned.yaml \
  --target 0.2          # legacy internal similarity target
  --min-quality 88.0    # provisional metric-specific constraint
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
3. Measures the legacy `cid_predict_self` heuristic and the **quality score** on
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

## Quality fallback limitation

When VMAF is unavailable, the current implementation falls back through the table
below. The rescaled values are **not equivalent**, so `--min-quality 88` does not
have one stable perceptual meaning across environments. Pin the backend and treat a
backend change as a different experiment.

| Priority | Metric | Source | Unified scale |
|---|---|---|---|
| 1 | VMAF | `libvmaf` filter | 0..100 verbatim |
| 2 | SSIM × 100 | `ssim` filter | `ssim_mean * 100` |
| 3 | pHash similarity × 100 | imagehash on 120 frames | `phash_similarity * 100` |

`core.qa.quality.quality_score` picks the first available metric and
returns a `QualityScore(score, metric)`. The HTML QA report shows which
one was used so the calibration log is auditable.

Do not use the fallback chain as a production quality gate. Phase 5 of the production
plan replaces it with explicit metric-specific constraints and registered references.

## Tuning the knobs

| Flag | Default | When to change |
|---|---|---|
| `--target` | 0.2 | Legacy internal self-similarity constraint; not an external-system probability |
| `--min-quality` | 88.0 | Keep fixed only when the metric backend/reference is also fixed |
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

1. **The heuristic is flat or non-monotone** — static scenes and transform
   interactions can violate the search assumption. Do not compensate by blindly
   increasing effect strength; inspect the rendered candidates.

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
- Calibrate or predict any external rights-management system. The legacy
  `cid_predict_self` field is an internal diagnostic only.

## Programmatic API

```python
from pathlib import Path
from yt_uniquifier.core.calibration.loop import calibrate, CalibrationTarget
from yt_uniquifier.core.profile_loader import load_profile, dump_profile

base = load_profile(Path("profiles/medium.yaml"))
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

If you have previous authorized derivatives indexed via
`yt-uniq corpus add`, the QA reports during calibration also flag
`corpus_matches`. Calibration itself uses only the legacy `cid_predict_self`
heuristic, not the corpus. Use `--vs-corpus` for regression/self-collision
diagnostics and review quality independently.
