# Seed strategy

Every stochastic transform — `video.crop_resize` (random kick within
`max_strength`), `video.rotate` (random angle within `degrees`),
`audio.compand.randomize_within`, `video.temporal_jitter.{blackout,drop}_offset`,
etc. — draws its random parameters from a `random.Random(run_seed)` seeded by
the `Plan.run_seed` value.

`Profile.seed_strategy` controls how that `run_seed` is chosen per
invocation, which in turn controls **reproducibility** vs **variability**.

## The four strategies

| Strategy | `run_seed` source | Behaviour |
|---|---|---|
| `fixed` | `Profile.seed` verbatim (0 if null) | every run produces an identical filter graph and (modulo encoder nondeterminism) identical output |
| `per_run` | `random.randrange(2**32)` per invocation | every run rolls a fresh seed — different transform-parameter draws on each invocation, same source |
| `per_file` | `sha256(str(source.path))[:4]` | deterministic from the source path string; same input → same seed, every time |
| `divergent` | fresh per-run base seed (like `per_run`) AND a per-segment seed derived as `sha256(plan_hash, segment_idx, run_seed)` | the run has a base seed (different each invocation), but adjacent segments within that run get **different** per-segment seeds |

`fixed`, `per_run`, and `per_file` set the seed once at run start;
`divergent` adds a second derivation step at segment time inside the
segmenter.

## Use cases

### `per_run` — generate N upload variants

Best for: re-uploading the same master content as multiple distinct
variants (e.g. for A/B thumbnail testing, multi-channel distribution).

```yaml
seed_strategy: per_run
```

```bash
for i in 1 2 3 4; do
  yt-uniq run master.mp4 --profile cid_aware.yaml --out uniq_v$i.mp4
done
```

Each run rolls a fresh seed → four different filter-parameter draws →
four distinct outputs.

If you re-run on the same `--work-dir`, the stored `run_seed` in
`state.json` is reused (so resume is byte-identical to the original run).
To force a re-roll, pass `--new-variant`.

### `per_file` — reproducible builds

Best for: build pipelines, regression testing, audit trails. The same
input always produces the same output, regardless of when or where you
run it.

```yaml
seed_strategy: per_file
```

```bash
yt-uniq run /movies/a.mp4 --profile p.yaml --out /out/a.mp4
# … 6 months later …
yt-uniq run /movies/a.mp4 --profile p.yaml --out /out/a_v2.mp4
sha256sum /out/a.mp4 /out/a_v2.mp4   # identical (modulo encoder nondeterminism)
```

The seed is derived from the path string, so `/movies/a.mp4` and
`/symlinks/a.mp4` get different seeds even if they're the same file.

### `fixed` — deterministic with explicit seed

Best for: investigation, debugging, comparing two profile changes against
the exact same random draws.

```yaml
seed_strategy: fixed
seed: 42
```

`Profile.seed` must be set (otherwise treated as 0). Useful when you
need *bit-stable* output across encoder versions, or when you want a
profile change's effect to be isolated from RNG noise.

### `divergent` — CID-aware default (v0.3.3+)

Best for: Content-ID divergence on long-form content. This is the v0.3.3
default for `cid_aware.yaml` and `cid_aggressive.yaml`.

```yaml
seed_strategy: divergent
```

The base seed for the run is rolled like `per_run`. Then at segment
build time, each segment derives its own seed:

```python
segment_seed = sha256(plan_hash + ":" + segment_idx + ":" + run_seed)[:4]
```

So:

- Two runs of the same source on the same profile → different base seeds
  → entirely different segment-by-segment parameter draws (good for
  variability).
- One run with N segments → N different segment seeds. Adjacent segments
  get different crop phases, different `temporal_jitter` offsets,
  different noise patterns. A temporal-aware CID detector trying to lock
  onto run-level uniformity gets a moving target.
- Resume of the same run → same base seed in `state.json` → same per-segment
  seeds reproduce. Resume is still byte-stable.

Why it matters: Fojcik & Syga (arXiv:2501.11171, 2025) showed that
temporal-aware video-copy-detection systems exploit **per-segment
similarity** between adjacent chunks. With a single run-wide seed, adjacent
segments share crop offsets, noise patterns, etc. — only the content
varies. With divergent seeds, the *transform parameters* also vary across
segments, making the per-chunk pHash distribution wider.

## Reproducibility matrix

| Scenario | `fixed` | `per_run` | `per_file` | `divergent` |
|---|---|---|---|---|
| Two runs, same source, no `--new-variant` | identical | identical (resume from `state.json`) | identical | identical (same base + same per-seg derivation) |
| Two runs, same source, `--new-variant` | identical | different | identical | different |
| Two runs, same source, different machines, no `state.json` | identical | different | identical | different |
| Two runs, same source, different `--work-dir` | identical | different | identical | different |

"identical" assumes no encoder-level nondeterminism (NVENC and some QSV
chips can produce slightly different output bitstreams across runs even
with the same input; libx264/libx265 are deterministic).

## What `run_seed` actually controls

The seed is plumbed into each transform's `build()` via
`call_build(spec, params, alloc, in_label, rng=Random(run_seed))`.
Transforms that take an `rng=` argument use it for their stochastic
parameters; transforms that don't (e.g. `video.speed`, `audio.eq` without
`randomize_bands`) ignore it.

For `divergent`, the segmenter wraps each segment with `_plan_for_segment`,
which substitutes `run_seed` with the per-segment derived seed before
calling `build_video_segment_command`. The main audio pass (loudnorm,
pitch, EQ) runs on the **full source** outside segmentation, so it always
uses the run-level seed — divergence only affects video.

## Where to find it in code

| Module | What it does |
|---|---|
| `core/models.py::SeedStrategy` | the `Literal` type that bounds the field |
| `core/models.py::Profile.seed_strategy` | the YAML-loaded value |
| `core/seed_resolver.py::resolve_run_seed` | maps strategy → uint32 run seed |
| `core/seed_resolver.py::derive_segment_seed` | sha256-based per-segment derivation for `divergent` |
| `core/segmenter.py::_plan_for_segment` | calls the derivation and returns a Plan copy |
| `core/orchestrator.py::build_plan` | invokes `resolve_run_seed` once at run start |

## Testing

`tests/unit/test_seed_resolver.py` covers the four strategies; the
v0.3.3 `tests/unit/test_divergent_seed.py` adds 6 tests around the
per-segment derivation (determinism, uniqueness, uint32 range, plan-copy
identity for non-divergent strategies, plan-copy diff for divergent).
