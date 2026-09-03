# Experimental calibration workflow

`yt-uniq calibrate` searches the intensity of an existing profile for processing
owned or licensed media. It compares the source with an authorized derivative using
local engineering diagnostics. It does not predict YouTube Content ID or any other
external rights-management system.

The command remains experimental: similarity thresholds are corpus-dependent, and a
short representative sample cannot prove full-file quality. Always run the tuned
profile on the full source and inspect the final QA report before publication.

## Quick start

```bash
yt-uniq calibrate /path/to/master.mp4 \
  --base src/yt_uniquifier/profiles/medium.yaml \
  --out /path/to/tuned.yaml \
  --target 0.2 \
  --min-quality 88.0 \
  --iterations 7 \
  --clip-sec 60.0 \
  --metric chromaprint

yt-uniq run /path/to/master.mp4 \
  --profile /path/to/tuned.yaml \
  --out /path/to/derivative.mp4
```

`chromaprint` needs `fpcalc`. `sscd` needs the optional `[ml]` dependencies and is
substantially heavier.

## Calibration v2 behavior

1. The total `--clip-sec` budget is divided across the beginning, middle, and end of
   a long source. A source shorter than the budget is used as one complete clip.
2. The three windows are stream-copied and concatenated into one content-keyed probe.
   This avoids spending three times the requested encode budget.
3. Every candidate uses `seed_strategy: fixed` and the same seed, so stochastic
   transform patterns do not move the objective between trials.
4. The search first measures factor `1.0`, the lower bound `0.25`, and the upper bound
   `4.0` (subject to the iteration budget). It then splits informative intervals on a
   logarithmic scale. It does not assume that a transform stack is monotone.
5. Each candidate goes through the existing `build_plan` and `run_full` pipeline.
   Encode/evaluation failure is retried once at the same factor and then aborts; it is
   never converted into a poor score.
6. Similarity and perceptual quality are independent constraints. A candidate is
   feasible only when both pass.
7. The quality backend used by the first successful trial is pinned for the search.
   If the backend changes between VMAF and SSIM, calibration aborts rather than
   comparing incompatible numbers.
8. Complete scored trials are cached atomically by plan hash, algorithm version, and
   similarity metric. Re-running an interrupted search can reuse measurements. A
   changed source, profile, encoder, seed, tool version, or metric gets a different
   key. Incomplete runner/output artifacts are session-isolated so concurrent GUI or
   CLI searches cannot corrupt each other. Custom programmatic evaluator callbacks
   deliberately bypass this cache.
9. Among feasible candidates, the result favors higher measured quality and then a
   gentler factor. If none is feasible, it returns the candidate with the smallest
   normalized constraint violation and exits with status 2.

The three anchor trials are intentional. Calibration therefore does not stop merely
because factor `1.0` passes; the extra evidence detects quality cliffs and
non-monotone behavior. Durable trial reuse offsets that cost on repeated runs.

## Quality threshold semantics

`--min-quality` is not a universal perceptual unit:

| Backend | Reported value | Typical availability |
|---|---:|---|
| VMAF | native 0..100 score | FFmpeg built with `libvmaf` |
| SSIM | mean SSIM × 100 | fallback when VMAF is unavailable |

pHash is not a perceptual-quality fallback. The CLI and GUI always show the backend
next to the value. Treat a VMAF experiment and an SSIM experiment as different
calibrations even if both use the numeric threshold `88`.

## Options

| Flag | Default | Engineering meaning |
|---|---:|---|
| `--target` | 0.2 | Maximum local source/derivative similarity diagnostic; not an external probability |
| `--min-quality` | 88.0 | Minimum score for the explicitly reported VMAF or SSIM backend |
| `--iterations` | 5 | Total bounded-search trial budget; use 7–9 for heterogeneous long-form sources |
| `--clip-sec` | 60.0 | Total time distributed over start/middle/end windows |
| `--metric` | chromaprint | `chromaprint` or `sscd` local diagnostic |
| `--encoder` | auto | Pin when the production encode will use a specific encoder |
| `--work-dir` | `.yt_uniq_calib` | Probe, candidate, work, and scored-trial cache directory |

## Reading the result

`converged` means that at least one measured candidate passed both constraints within
the requested factor bounds. It does not mean the full movie has been qualified.

`not converged` means no sampled candidate passed both gates. Common causes are a
flat/noisy similarity diagnostic, a quality-fragile source, an unsuitable base
profile, or too small an iteration budget. Inspect the per-step backend and notes;
do not compensate by blindly raising transform strength.

The work directory is reusable. A step marked `scored-trial cache hit` was not
re-encoded. Delete only that calibration work directory if you intentionally need a
fresh measurement with otherwise identical inputs.

## Programmatic API

```python
from pathlib import Path

from yt_uniquifier.core.calibration.loop import CalibrationTarget, calibrate
from yt_uniquifier.core.profile_loader import dump_profile, load_profile

base = load_profile(Path("profiles/medium.yaml"))
result = calibrate(
    input_path=Path("/movies/master.mp4"),
    base_profile=base,
    target=CalibrationTarget(
        max_self_match=0.15,
        min_quality=86.0,
        max_iterations=8,
        test_clip_sec=90.0,
        seed=0,
        min_factor=0.25,
        max_factor=4.0,
    ),
    work_dir=Path("/tmp/calib"),
)

print(result.converged, result.factor, result.final_quality_metric)
dump_profile(result.profile, Path("tuned.yaml"))
```

`CalibratedResult.steps` records every sampled factor, profile, similarity score,
quality value/backend, elapsed scoring time, and cache/fallback note. Bounds are
validated before any media work and must satisfy
`0 < min_factor <= 1 <= max_factor`.

Calibration only scales parameters with an existing intensity rule. It does not
toggle transforms, reorder the filter graph, choose a second pipeline, or replace
full-output correctness and quality validation.
