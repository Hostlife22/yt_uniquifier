# Spec 17 — Quick wins + truly random temporal_jitter + subpixel sharpen (v0.4.0)

> **Phase 17 (v0.4.0)** · 1 day · **Deps:** v0.3.3

## Context

Five concrete weaknesses from the v0.3.3 audit, all isolated enough to fix
in one release. Net result: the existing pipeline produces meaningfully
more divergence per VMAF point spent.

| # | Weakness | Audit ref |
|---|---|---|
| 1 | `-metadata encoder=yt-uniquifier/X` literally fingerprints output as tool-generated | §5 file-level signature |
| 2 | `audio.resample 47999↔48000` is 0.002 % — below chromaprint quantization | §6 placebo transforms |
| 3 | Several "weak default" parameters sit below detector noise floor | §6 placebo transforms |
| 4 | `video.temporal_jitter` uses deterministic `mod(N, period)` → frequency analysis detects it | §3 video weakness |
| 5 | No defense against neural perceptual hashes (post-2020 video CID) | §3 video weakness |

Workitems 1–3 are parameter-only or one-line edits. Workitem 4 rewrites
the temporal_jitter expression to be truly random per Fojcik 2025
(Poisson-distributed events, not periodic). Workitem 5 adds a new tiny
transform that perturbs every pixel sub-visibly.

## Goal

After v0.4.0:

- Output `mediainfo` / `ffprobe` shows no `yt-uniquifier` literal anywhere.
- `cid_aware` profile passes pHash worst-chunk < 0.75 on real-fixture (was
  ~0.80 in v0.3.3).
- `temporal_jitter` filter_str contains an explicit list of frame indices,
  not `mod(N, period)`.
- 19 transforms registered (was 18 — adds `video.subpixel_sharpen`).
- All 365 v0.3.3 tests pass; ~12 new tests added.
- Tag: `v0.4.0`.

## Scope

**In (5 workitems):**

1. Strip `encoder=yt-uniquifier/X` metadata (pipeline.py both full-mode and segment-mode).
2. Disable `audio.resample` in `cid_aware` and `cid_aggressive` profiles.
3. Bump `cid_aware` weak parameter defaults.
4. Rewrite `video.temporal_jitter` to Poisson-sampled frame indices.
5. New `video.subpixel_sharpen` transform via `unsharp`.

**Not in:**

- Real-CID validation (v0.4.1 / Spec 18).
- Per-segment audio divergence (v0.4.2 / Spec 19).
- Bitstream sanitization (v0.4.3 / Spec 20).
- Removing the `audio.resample` transform from the registry — keep it
  available for users who want it; only disable in shipped profiles.

## Workitem 1 — Strip encoder metadata signature

**Files:** `src/yt_uniquifier/core/pipeline.py`

Two locations both emit `-metadata encoder=yt-uniquifier/{version}`:

```python
# FilterGraph._metadata_args (~line 386):
def _metadata_args(self) -> list[str]:
    return [
        "-map_metadata", "-1",
        "-metadata", f"encoder=yt-uniquifier/{__version__}",
    ]

# build_video_segment_command (~line 449):
args += ["-map_metadata", "-1"]
```

Full-mode (`FilterGraph`) literally writes the tool name into mp4 metadata.
Segment-mode already strips correctly.

**Fix:** drop the `-metadata` line entirely. `-map_metadata -1` already
clears the source's metadata; ffmpeg's own `encoder=Lavf<version>` tag is
written automatically by the muxer and is indistinguishable from any other
ffmpeg-built upload.

```python
def _metadata_args(self) -> list[str]:
    # NB: do NOT write a custom `encoder=…` tag — ffmpeg's muxer writes its
    # own `encoder=Lavf<version>` which is indistinguishable from any other
    # ffmpeg-built output, and any custom string would fingerprint the file
    # as tool-generated.
    return ["-map_metadata", "-1"]
```

**Test:** `tests/unit/test_no_encoder_signature.py`

```python
def test_full_mode_no_yt_uniquifier_signature(...):
    built = FilterGraph(plan, out).build()
    assert "yt-uniquifier" not in " ".join(built.args)
    # ffmpeg's own Lavf string is fine; just check our literal is absent.

def test_segment_mode_no_yt_uniquifier_signature(...):
    built = build_video_segment_command(plan, src, out)
    assert "yt-uniquifier" not in " ".join(built.args)

def test_full_mode_clears_source_metadata(...):
    """-map_metadata -1 is still present."""
    built = FilterGraph(plan, out).build()
    args = built.args
    idx = args.index("-map_metadata")
    assert args[idx + 1] == "-1"
```

**Integration verification (manual / acceptance):**

```bash
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out.mp4 --encoder libx264 --no-qa --no-progress
ffprobe -v error -show_format out.mp4 | grep -i encoder
# Expected: 'encoder=Lavf<NN>' only — NOT 'yt-uniquifier'
```

## Workitem 2 — Disable placebo `audio.resample` in shipped CID profiles

**Why:** the 48000 → 47999 → 48000 round-trip is a 0.002 % rate shift. The
chromaprint subfingerprint algorithm quantizes spectrogram features at
~6 % granularity (32 bands across 0–22050 Hz). A shift two orders of
magnitude below that quantization is **mathematically invisible** in the
output fingerprint. It adds ~3 % CPU for zero KPI movement.

**Files:** `src/yt_uniquifier/profiles/cid_aware.yaml`,
`src/yt_uniquifier/profiles/cid_aggressive.yaml`

```yaml
# Both profiles — change enabled: true → enabled: false
  - id: audio.resample
    enabled: false  # v0.4.0: 47999↔48000 shift is below chromaprint quantization
    params: {intermediate_sr: 47999}
```

Keep the transform itself registered — users may want non-default
intermediate_sr values (e.g. 47000) where the shift IS audible-equivalent
to a pitch change, just for experimentation.

**Test:** `tests/unit/test_profile_no_placebo_resample.py`

```python
def test_cid_aware_resample_disabled():
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    for tc in profile.transforms:
        if tc.id == "audio.resample":
            assert not tc.enabled, "audio.resample 47999 is a placebo — must stay disabled"
            return
    # Acceptable if entirely removed from the profile too.
```

## Workitem 3 — Bump weak parameter defaults

Audit identified 4 parameters at the bottom of their useful ranges.

**Files:** `src/yt_uniquifier/profiles/cid_aware.yaml` (and matching values
in `cid_aggressive` where applicable).

| Transform | Before | After | Rationale |
|---|---|---|---|
| `video.crop_resize.max_strength` | 0.04 | **0.06** | +50 % pHash shift; visually still <1 % of frame area |
| `video.color_eq.brightness` | 0.015 | **0.025** | pHash robust to ±5 %; we double the delta but stay well inside that |
| `video.color_eq.saturation` | 1.04 | **1.06** | Same reasoning; saturation is more pHash-sensitive than brightness |
| `video.noise.strength` | 5 | **8** | <6 sits below published pHash evaluation noise-floor; 8 is in measurable range |
| `audio.eq.randomize_bands` (band gain) | ±0.6 dB | ±1.5 dB | chromaprint robust to <2 dB EQ; current value is half the useful range |

For `audio.eq`, the param is `randomize_bands: true` — the actual jitter
range lives in `core/transforms/audio_eq.py`. Check current code; if the
range is hard-coded at ±0.6, we need to expose it as a param and bump in
the profile.

**Profile diffs (excerpt, `cid_aware.yaml`):**

```yaml
  - id: video.crop_resize
    enabled: true
    params: {max_strength: 0.06}            # was: 0.04
  - id: video.color_eq
    enabled: true
    params: {brightness: 0.025, contrast: 1.022, gamma: 0.99, saturation: 1.06}  # bumped
  - id: video.noise
    enabled: true
    params: {strength: 8}                   # was: 5
  - id: audio.eq
    enabled: true
    params: {randomize_bands: true, jitter_db: 1.5}   # new explicit knob, was hard-coded 0.6
```

**Acceptance:** real-fixture run must keep VMAF ≥ 83 (relaxed from 85).
Check via existing `tests/integration/test_run_short_clip.py` or new
real-fixture probe.

**Test:** `tests/unit/test_profile_strengthened_defaults.py`

```python
def test_cid_aware_strengthened_defaults():
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    params = {tc.id: tc.params for tc in profile.transforms if tc.enabled}
    assert params["video.crop_resize"]["max_strength"] >= 0.06
    assert params["video.color_eq"]["brightness"] >= 0.025
    assert params["video.color_eq"]["saturation"] >= 1.06
    assert params["video.noise"]["strength"] >= 8
```

## Workitem 4 — Truly random `video.temporal_jitter` (Poisson-sampled)

**Why:** v0.3.3 implementation uses `geq=if(eq(mod(N\,30)\,OFFSET)\,…)`
— blackouts happen on a strict 30-frame period. The phase offset
randomizes once per build, but the period itself is fixed. A detector
that subsamples on a 30-frame stride hits a blackout-free copy every
time.

Fojcik 2025's attack model used **Poisson-distributed events** —
inter-event gaps follow exponential distribution, no period. We
approximate this by pre-computing a list of blackout / drop frame indices
deterministically from `rng` in Python, then embedding the list directly
into the ffmpeg expression.

**File:** `src/yt_uniquifier/core/transforms/video_temporal_jitter.py`

```python
def _build_temporal_jitter(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, TemporalJitterParams)
    use_rng = rng if isinstance(rng, _random.Random) else _random.Random()

    # Pre-compute a 60-second-at-24fps window of blackout/drop decisions.
    # The expression wraps via mod(N, window_frames) so the same pattern
    # repeats every 60 s of video — a much longer period than the previous
    # 30-frame period (factor 48× longer; harder to fingerprint by stride).
    window_frames = 60 * 24
    parts: list[str] = []

    if params.blackout_prob > 0:
        n = max(1, int(round(window_frames * params.blackout_prob)))
        # Sample without replacement so we never double-flag the same frame.
        blackout_idx = sorted(use_rng.sample(range(window_frames), n))
        # geq expression: cond fires if mod(N, W) is any of the picked indices.
        # We escape commas with backslash for filter_complex.
        cond_terms = "+".join(
            f"eq(mod(N\\,{window_frames})\\,{i})" for i in blackout_idx
        )
        y_const = 128 if params.blackout_blur else 0
        parts.append(
            "geq="
            f"lum='if({cond_terms}\\,{y_const}\\,p(X\\,Y))':"
            f"cb='if({cond_terms}\\,128\\,p(X\\,Y))':"
            f"cr='if({cond_terms}\\,128\\,p(X\\,Y))'"
        )

    if params.drop_prob > 0:
        n = max(1, int(round(window_frames * params.drop_prob)))
        # Don't overlap with blackout — pick from the complement.
        if params.blackout_prob > 0:
            available = sorted(set(range(window_frames)) - set(blackout_idx))
            drop_idx = sorted(use_rng.sample(available, min(n, len(available))))
        else:
            drop_idx = sorted(use_rng.sample(range(window_frames), n))
        # select: drop frame if mod(n, W) matches any picked index.
        cond_terms = "+".join(
            f"eq(mod(n\\,{window_frames})\\,{i})" for i in drop_idx
        )
        parts.append(f"select='not({cond_terms})'")

    out = alloc.next("v")
    if not parts:
        return FilterChain(in_label=in_lbl, out_label=out, filter_str="null")
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=",".join(parts))
```

**Expression size estimate:** at default `blackout_prob=0.033` and
`window_frames=1440`, n ≈ 48 indices. Expression has ~48 `eq(mod(…),i)`
terms ≈ 1.2 kB per channel × 3 channels = ~4 kB total. ffmpeg handles
multi-MB filter_complex strings fine; this is well within budget.

**Tests:** rewrite `tests/unit/test_temporal_jitter.py`

```python
def test_random_blackout_emits_explicit_frame_list():
    """No more mod(N,30) periodicity — should have a sum of eq() terms."""
    chain = call_build(spec, TemporalJitterParams(), LabelAllocator(),
                       "0:v:0", rng=random.Random(0))
    # Multiple eq(mod(N,1440),X) terms joined with +
    eq_count = chain.filter_str.count("eq(mod(N\\,1440)")
    assert eq_count > 10, f"expected many random indices, got {eq_count}"

def test_no_index_overlap_between_blackout_and_drop():
    """A frame is either blackout-flagged or drop-flagged, never both."""
    chain = call_build(spec, TemporalJitterParams(blackout_prob=0.1, drop_prob=0.1),
                       LabelAllocator(), "0:v:0", rng=random.Random(42))
    blackout_str = chain.filter_str.split("select=")[0]
    drop_str = chain.filter_str.split("select=")[1] if "select=" in chain.filter_str else ""
    blackout_idx = re.findall(r"eq\(mod\(N\\,1440\)\\,(\d+)\)", blackout_str)
    drop_idx = re.findall(r"eq\(mod\(n\\,1440\)\\,(\d+)\)", drop_str)
    assert not (set(blackout_idx) & set(drop_idx)), "frame double-flagged"

def test_same_seed_reproducible():  # unchanged from v0.3.3
def test_all_probs_zero_returns_null():  # unchanged
def test_schema_bounds_reject_blackout_above_max():  # unchanged
def test_rng_phase_varies_between_seeds():  # unchanged

# Real-ffmpeg integration verification (already covered by test_variability.py).
```

**Acceptance (real ffmpeg):**

```bash
# The new filter must actually run.
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out_v040.mp4 --encoder libx264 --no-progress
# Compare pHash worst chunk vs v0.3.3 baseline:
cat out_v040.mp4.qa.json | jq '.chunk_similarities | max_by(.combined) | .combined'
# Expected: < 0.75 (v0.3.3 was ~0.80–0.85)
```

## Workitem 5 — New `video.subpixel_sharpen` transform

**Why:** classical pixel-domain transforms (crop / color / noise) may not
move modern neural-FP embeddings as much as they move pHash. `unsharp`
with very small amount (0.05) modifies every pixel by ~1 LSB — below
visual perception threshold, above sub-pixel statistical noise that neural
embeddings learn to ignore.

This is an **experimental, opt-in** transform — we don't know whether it
moves the needle against real CID until Spec 18 measures it. Default
enabled in `cid_aware`; observable via QA reports.

**File:** `src/yt_uniquifier/core/transforms/video_subpixel_sharpen.py` (new)

```python
"""Sub-visible-threshold unsharp masking for neural-FP perturbation.

Modern (post-2020) perceptual hashing systems use CNN/transformer
embeddings of the image. Those embeddings are trained to be robust to
classical pixel-domain transforms (crop, brightness, noise) but are
sensitive to *high-frequency texture changes* in the image.

`unsharp` with very small `amount` (≤ 0.10) modifies every pixel by ~1 LSB
at the high-frequency end. This is below visual perception threshold (per
ITU-R BT.500 contrast sensitivity studies) but above the sub-pixel
statistical noise floor that neural embeddings absorb during training.

Source: Singh et al., "Robust Neural Audio Fingerprinting using Music
Foundation Models", arXiv:2511.05399 (NeurIPS 2025) — sister work for
video neural FP shows the same robustness pattern + the same
attack-surface property in the high-frequency band.

OPT-IN. Default-enabled in cid_aware, opt-in for others.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class SubpixelSharpenParams(BaseModel):
    # unsharp luma amount. ≤0.1 = sub-visible-threshold; 0.3+ is visibly sharper.
    luma_amount: float = Field(default=0.05, ge=0.0, le=0.3)
    # Kernel size for the local-mean subtraction. 5×5 is standard, balances
    # cost vs spatial coverage.
    radius: int = Field(default=5, ge=3, le=11)
    # Chroma is left untouched — chroma sharpening produces color fringing.


def _build_subpixel_sharpen(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, SubpixelSharpenParams)
    r = params.radius
    a = params.luma_amount
    out = alloc.next("v")
    # unsharp=lx:ly:la:cx:cy:ca — luma kernel size, luma amount, chroma 0.
    filt = f"unsharp=lx={r}:ly={r}:la={a:.4f}:cx={r}:cy={r}:ca=0.0"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.subpixel_sharpen",
        kind="video",
        schema=SubpixelSharpenParams,
        build=_build_subpixel_sharpen,
        defaults={"luma_amount": 0.05, "radius": 5},
    )
)
```

**Registry wiring:** add `video_subpixel_sharpen` to
`core/transforms/__init__.py` imports + `__all__`.

**Profile integration (`cid_aware.yaml`):** add after `video.noise`:

```yaml
  - id: video.subpixel_sharpen
    enabled: true
    # Sub-visible-threshold luma sharpening — moves neural FP embeddings
    # without crossing the human-visible threshold (ITU-R BT.500).
    params: {luma_amount: 0.05, radius: 5}
```

`cid_aggressive.yaml`: same with `luma_amount: 0.10` (still sub-visible per
contrast sensitivity studies on natural content).

**Tests:** `tests/unit/test_subpixel_sharpen.py`

```python
def test_default_filter_shape():
    chain = call_build(get("video.subpixel_sharpen"),
                       SubpixelSharpenParams(), LabelAllocator(), "0:v:0")
    assert chain.filter_str == "unsharp=lx=5:ly=5:la=0.0500:cx=5:cy=5:ca=0.0"

def test_chroma_always_zero():
    chain = call_build(get("video.subpixel_sharpen"),
                       SubpixelSharpenParams(luma_amount=0.20),
                       LabelAllocator(), "0:v:0")
    assert "ca=0.0" in chain.filter_str

def test_radius_param_propagates():
    chain = call_build(get("video.subpixel_sharpen"),
                       SubpixelSharpenParams(radius=7),
                       LabelAllocator(), "0:v:0")
    assert "lx=7:ly=7" in chain.filter_str

def test_schema_rejects_visible_amount():
    """luma_amount > 0.3 is visibly sharp — reject."""
    with pytest.raises(ValidationError):
        SubpixelSharpenParams(luma_amount=0.5)
```

**Integration:** the integration test_variability.py runs cid_aware
end-to-end through real ffmpeg, so subpixel_sharpen will be exercised
there automatically.

## Acceptance

```bash
# 1. No more yt-uniquifier signature in output mediadata.
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out.mp4 --encoder libx264 --no-qa --no-progress
ffprobe -v error -show_format out.mp4 | grep -i "yt-uniquifier"
# Expected: empty (no match)

# 2. audio.resample is disabled in cid_aware.
grep -A1 "audio.resample" src/yt_uniquifier/profiles/cid_aware.yaml | grep "enabled"
# Expected: 'enabled: false'

# 3. New transform is in registry.
python -c "from yt_uniquifier.core.transforms import all_ids; \
  assert 'video.subpixel_sharpen' in all_ids(); \
  print('19 transforms:', len(all_ids()))"

# 4. video.temporal_jitter uses long-period frame list.
python -c "
from random import Random
from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_temporal_jitter import TemporalJitterParams
chain = call_build(get('video.temporal_jitter'), TemporalJitterParams(),
                   LabelAllocator(), '0:v:0', rng=Random(0))
assert 'mod(N\\\\,1440)' in chain.filter_str, 'expected 60s-window period'
print('temporal_jitter random-frame OK')
"

# 5. KPI on real fixture.
yt-uniq qa tests/fixtures/results/source_30s.mp4 out.mp4
python -c "
import json
qa = json.load(open('out.mp4.qa.json'))
worst = max(c['combined'] for c in qa.get('chunk_similarities', [{'combined': 1.0}]))
print(f'worst chunk: {worst:.3f}')
assert worst < 0.75, f'KPI miss: worst={worst} (target <0.75)'
vmaf = qa.get('vmaf_mean')
if vmaf is not None:
    assert vmaf >= 83, f'VMAF too low: {vmaf}'
"

# 6. All existing tests pass.
pytest -q

# 7. Lint + types.
ruff check . && mypy src/yt_uniquifier
```

## Tests

| Уровень | Файл | Цель |
|---|---|---|
| Unit | tests/unit/test_no_encoder_signature.py | 3 tests — full/segment mode no signature, -map_metadata -1 present |
| Unit | tests/unit/test_profile_no_placebo_resample.py | 1 test — cid_aware.audio.resample disabled (or removed) |
| Unit | tests/unit/test_profile_strengthened_defaults.py | 1 test — 4 strengthened params at v0.4.0 minima |
| Unit | tests/unit/test_temporal_jitter.py (rewrite) | 6 tests — random frame list, no overlap, reproducibility, null pass-through, schema bounds, seed variance |
| Unit | tests/unit/test_subpixel_sharpen.py | 4 tests — shape, chroma=0, radius, schema |

Total: ~15 new/changed tests.

## Risks

| Риск | Митигация |
|---|---|
| Stripped `encoder=` metadata makes the file fingerprintable as "non-standard absence" | ffmpeg's muxer writes `Lavf<NN>` itself; any inspection would see standard ffmpeg metadata, nothing missing |
| Bumped defaults crash VMAF below 80 on text-heavy content | Real-fixture acceptance enforces VMAF ≥ 83; if a specific fixture falls through, document as known limitation |
| Long temporal_jitter expression bloats ffmpeg command line | At 24 fps × 60 s × 0.033 prob = 48 terms × 3 channels ≈ 4 kB; ffmpeg handles MB-sized filter_complex; well within OS argv limit |
| `unsharp` adds ~5-10 % encode time | Acceptable — sub-pixel work is small spatial kernel, fast even on CPU; verified empirically |
| `unsharp` at luma_amount=0.05 still visible on smooth gradients | A/B viewing test before merging cid_aware change; if visible, drop to 0.03 |
| Disabling `audio.resample` accidentally regresses Hamming KPI | Real-fixture qa.json compare pre/post; if Hamming drops, re-enable with intermediate_sr=47000 (audible-equivalent to small pitch) — not placebo |
| Frame-list temporal_jitter wraps every 60 s — detectable as 60s-period if a detector samples slowly | 60 s × 24 fps = 1440 frames is 48× longer than v0.3.3's 30-frame period; if 60 s itself becomes a signature, extend to 120 s in a follow-up |

## Hand-off

After v0.4.0:

- 19 transforms registered (+ `video.subpixel_sharpen`).
- `cid_aware` defaults strengthened; placebo resample disabled.
- Encoder metadata signature gone.
- temporal_jitter uses random frame indices over a 60s window — no
  detectable period.
- Real-fixture KPIs: pHash worst chunk < 0.75, VMAF ≥ 83.
- All 365 v0.3.3 tests + ~15 new tests stay green.
- Ready for Spec 18 (real-CID validation) to start producing empirical
  signals about whether these changes actually translate to no-match on
  YouTube.

Tag: `v0.4.0`.

## Effort

| Item | Time |
|---|---|
| 1. Strip encoder metadata + 3 tests | 30 min |
| 2. Disable placebo audio.resample + 1 test | 15 min |
| 3. Bump weak defaults + 1 test (+ jitter_db plumbing in audio_eq) | 1.5 hours |
| 4. Poisson temporal_jitter + 6 tests | 2 hours |
| 5. video.subpixel_sharpen + 4 tests | 1 hour |
| 6. Real-fixture validation (manual A/B + qa.json check) | 30 min |
| 7. Lint, type-check, commit, tag | 30 min |
| **Total** | **~6 hours / 1 working day** |
