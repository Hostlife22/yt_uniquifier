# Spec 16 — Temporal jitter, audio FP delta, divergent seeds, noise overlay (v0.3.3)

> **Phase 16 (v0.3.3)** · 4 days · **Deps:** v0.3.2 (Spec 15)

## Context

v0.3.2 closes the verified pitch threshold hole. v0.3.3 layers four
academically-verified evasion mechanisms onto that baseline:

1. **Random temporal frame jitter** — Fojcik & Syga, arXiv:2501.11171 (2025)
   demonstrated 60%+ μAP drop in Meta VSC2022 baseline under random frame
   blackout (`p=1/10`) and speed jitter. Reproducible in pure ffmpeg.

2. **Audio fingerprint Hamming delta as explicit KPI** — we already compute
   chromaprint when fpcalc is installed, but bit-level distance to source
   isn't surfaced in `qa.json`. Smitelli (2010) showed audio is the most
   predictive CID channel; this measurement makes the parameter that matters
   most directly observable.

3. **Per-segment seed divergence** — currently one seed per run means
   adjacent segments get nearly-identical transform parameters. A
   temporal-aware detector matching chunk-by-chunk can hit on any one
   chunk. Fojcik 2025's temporal attacks rely on this; if we don't fix it,
   our concat output is more uniform than necessary.

4. **Parametric audio noise overlay** — Smitelli showed `≥45%` white noise
   in the mix breaks CID. 45% destroys intelligibility, but 20–30% shifts
   chromaprint while keeping speech understandable.

All four are pure-ffmpeg / pure-Python, no new dependencies, no ML.

## Goal

After v0.3.3:

- `video.temporal_jitter` registered; default-enabled in `cid_aware` with
  conservative probabilities (blackout 1/30, dup 1/40).
- `qa.json` gains `audio_fp_hamming_per_frame` and
  `audio_fp_match_confidence` fields when fpcalc is available.
- `Profile.seed_strategy` accepts new value `"divergent"`; pipeline derives
  per-segment seed from `hash(plan_hash, segment_index)`.
- `audio.noise_overlay` registered; opt-in via `cid_aggressive` only.
- Tag: `v0.3.3`.

## Scope

**In (4 workitems):**

1. `core/transforms/video_temporal_jitter.py` — new transform with
   `blackout_prob`, `dup_prob`, `drop_prob`, `max_per_minute` params.
2. `core/qa/audio_fp.py` extension — return Hamming distance per frame;
   `core/qa/report.py` wires it into `QAReport` and HTML template.
3. `core/seed_resolver.py` extension — new strategy `"divergent"`;
   `core/segmenter.py` propagates per-segment seed; pipeline accepts seed
   override per-segment.
4. `core/transforms/audio_noise_overlay.py` — new transform with
   `noise_mix_db` (default -10 dB ≈ 30% mix) and `randomize_within_db`.

**Not in (deferred to v0.4):**

- `video.lut3d` (user-supplied .cube).
- Platform-specific profiles (`cid_aware_tiktok.yaml`, etc.).
- AV1 grain synthesis.
- `video.variable_speed_segments` (covered conceptually by
  `temporal_jitter` + future scene-aware variant).

## Workitem 1 — `video.temporal_jitter`

**Source:** Fojcik & Syga, "Counteracting Temporal Attacks in Video Copy
Detection", arXiv:2501.11171 (2025). The attacks they study **as
defenders** are exactly the techniques we want **as attackers** against
fingerprinting. Verified: random blackout p=1/10 + speed jitter dropped
μAP by 60%+ on Meta's VSC2022 baseline.

**File:** `src/yt_uniquifier/core/transforms/video_temporal_jitter.py`

```python
"""Random temporal frame perturbation — blackout / duplicate / drop.

Per Fojcik & Syga (2025), random blackouts at probability 1/10 reduce
neural video-copy-detector mean Average Precision by 60%+ on Meta's
VSC2022 benchmark. Frame-level duplicate or drop has similar effect on
detectors that rely on fixed-stride sampling.

We keep probabilities low (1/30–1/60) by default to stay invisible on
playback; the source paper used 1/10, which is visible.

Filter strategy:
  - blackout: select frames where rand()<p, replace via `drawbox=fill`
  - dup: select frames where rand()<p, double via `setpts` / `tpad`
  - drop: select frames where rand()<p, drop via `select`

All three are time-domain perturbations: total file duration changes
slightly (dup adds, drop subtracts; blackout doesn't change duration).
For batch CID resistance we want random — but with a `max_per_minute`
cap so the user knows the worst case visible disruption.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class TemporalJitterParams(BaseModel):
    # Per-frame probability of each operation. Defaults are conservative —
    # at 24fps, blackout_prob=1/30 ≈ 0.8 jittered frames per second.
    blackout_prob: float = Field(default=0.033, ge=0.0, le=0.2)
    dup_prob: float = Field(default=0.025, ge=0.0, le=0.2)
    drop_prob: float = Field(default=0.025, ge=0.0, le=0.2)
    # Use blur instead of pure black for blackout (less visible).
    blackout_blur: bool = True


def _build_temporal_jitter(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, TemporalJitterParams)
    out = alloc.next("v")
    # We use ffmpeg's `random()` in select / drawbox expressions so each
    # frame independently decides. rng (run_seed) is plumbed via setting
    # `setpts=...` deterministic later if needed.
    parts: list[str] = []
    if params.blackout_prob > 0:
        if params.blackout_blur:
            # Strong blur when probability fires — visually a "smear", not a flash.
            parts.append(
                f"split=2[a][b];"
                f"[b]gblur=sigma=15[blur];"
                f"[a][blur]blend=all_expr='if(lt(random(0),{params.blackout_prob}),B,A)'"
            )
        else:
            parts.append(
                f"geq='if(lt(random(0),{params.blackout_prob}),0,p(X,Y))'"
            )
    if params.dup_prob > 0:
        # Duplicate via tpad: extends select-matching frames by one.
        parts.append(
            f"tpad=stop_mode=clone:stop_duration='if(lt(random(0),{params.dup_prob}),"
            f"1/r,0)'"
        )
    # drop_prob: select frames where rand >= prob (i.e. keep with 1-prob)
    if params.drop_prob > 0:
        parts.append(
            f"select='gte(random(0),{params.drop_prob})'"
        )
    if not parts:
        # Degenerate: nothing enabled. Pass-through.
        return FilterChain(in_label=in_lbl, out_label=out, filter_str="null")
    filt = ",".join(parts)
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="video.temporal_jitter",
        kind="video",
        schema=TemporalJitterParams,
        build=_build_temporal_jitter,
        defaults={
            "blackout_prob": 0.033, "dup_prob": 0.025, "drop_prob": 0.025,
            "blackout_blur": True,
        },
    )
)
```

⚠️ **Caveat:** ffmpeg's `random()` returns a per-call random number;
combined with `select` / `geq` / `tpad`, exact behaviour depends on
ffmpeg internals. The unit test verifies *the emitted filter_complex
string*; the integration test verifies the run completes and total
duration shift is bounded. Real-CID effectiveness is measured by the
v0.3.3 acceptance metrics (worst-chunk pHash should drop notably).

**Registry:** add `video_temporal_jitter` to `transforms/__init__.py`.

**Profile integration (`cid_aware.yaml`):** add after `video.noise`:

```yaml
- id: video.temporal_jitter
  enabled: true
  params: {blackout_prob: 0.033, dup_prob: 0.020, drop_prob: 0.020, blackout_blur: true}
```

`cid_aggressive.yaml`: enabled with `blackout_prob: 0.05, dup_prob: 0.04,
drop_prob: 0.04`.

**Tests (`tests/unit/test_temporal_jitter.py`):**

1. `test_default_emits_three_subexprs` — blackout + dup + drop all in filter_str.
2. `test_blackout_blur_uses_gblur_chain` — `blackout_blur=true` → contains `gblur`.
3. `test_blackout_blur_false_uses_geq` — `blackout_blur=false` → contains `geq`.
4. `test_all_probs_zero_returns_null` — `null` pass-through filter.
5. `test_probs_clamped_at_schema_bounds` — `blackout_prob=0.5` rejected (le=0.2).

## Workitem 2 — Audio FP Hamming delta as explicit KPI

**Source:** Smitelli (2010) — audio is the most predictive CID channel.
NeurIPS 2025 — platforms moving to neural FP; we should make audio
similarity a first-class measured signal so we have a feedback loop.

**File:** `src/yt_uniquifier/core/qa/audio_fp.py` extension

Add a new function returning per-frame Hamming distance:

```python
@dataclass(frozen=True)
class AudioFPDelta:
    available: bool
    hamming_per_frame_bits: float | None     # 0..32; ≥30 = high-confidence non-match
    match_confidence: float | None           # 1 - (mean_hamming / 32)
    note: str | None = None


def compare_hamming(input_path: Path, output_path: Path) -> AudioFPDelta:
    """Bit-level Hamming distance between chromaprint frames.

    Each chromaprint subfingerprint is a 32-bit integer. We pair frame i
    from input with frame i from output and count bits-different, averaged
    over all paired frames.

    Hamming distance ≥ 30 bits per 32-bit frame is "high confidence
    non-match" by chromaprint literature heuristics. ≤ 5 bits per frame
    is "high confidence match".
    """
    # Reuse _run_fpcalc internal helper; decode base64 + XOR per pair;
    # popcount the result; average across frames.
```

**File:** `src/yt_uniquifier/core/models.py` — extend `QAReport`:

```python
class QAReport(BaseModel):
    # ... existing fields ...
    audio_fp_hamming_per_frame: float | None = None     # bits in [0, 32]
    audio_fp_match_confidence: float | None = None      # [0, 1]
```

**File:** `src/yt_uniquifier/core/qa/report.py` — call `compare_hamming`
when chromaprint is available; populate the two new QAReport fields;
add a row to the HTML report.

**Tests (`tests/unit/test_audio_fp_hamming.py`):**

1. `test_identical_fingerprints_zero_hamming` — same fp bytes → mean=0.
2. `test_inverted_fingerprints_max_hamming` — XOR-inverted → mean=32.
3. `test_partial_overlap_intermediate` — known pattern → known mean.
4. `test_unavailable_when_fpcalc_missing` — graceful skip; `available=False`.
5. `test_match_confidence_normalisation` — confidence = 1 - mean/32.

**HTML template update:** add a row to `qa_report.html.j2`:

```
Audio FP Hamming distance: 28.4 bits/frame (confidence: high non-match)
```

Colour-code: green if ≥ 25 bits, yellow if 10–25, red if < 10.

## Workitem 3 — Divergent per-segment seed

**Source:** Fojcik 2025 (temporal attacks exploit uniform segment-level
similarity); general temporal-evasion literature.

**File:** `src/yt_uniquifier/core/models.py` — extend `SeedStrategy`:

```python
SeedStrategy = Literal["fixed", "per_run", "per_file", "divergent"]
```

**File:** `src/yt_uniquifier/core/seed_resolver.py` — handle new value:

```python
def resolve_run_seed(profile: Profile, source: SourceMeta) -> int:
    # ... existing strategies ...
    if profile.seed_strategy == "divergent":
        # Same effective behaviour as per_run at run scope; per-segment
        # divergence is applied in segmenter.py when building each segment.
        return random.randrange(_UINT32_MAX)
```

**File:** `src/yt_uniquifier/core/segmenter.py` — when building each
segment's plan, derive a segment-specific seed:

```python
import hashlib

def _segment_seed(plan_hash: str, segment_index: int, run_seed: int) -> int:
    """Deterministic per-segment seed derived from (plan_hash, segment_idx)."""
    payload = f"{plan_hash}:{segment_index}:{run_seed}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:4], "big", signed=False)


# In process_video_segment, before calling pipeline build:
if plan.profile.seed_strategy == "divergent":
    seg_seed = _segment_seed(plan.plan_hash, segment.idx, plan.run_seed)
    plan_for_segment = plan.model_copy(update={"run_seed": seg_seed})
else:
    plan_for_segment = plan
```

**Profile (`cid_aware.yaml`):** add at top level:

```yaml
seed_strategy: divergent
```

(Was `per_run`. `cid_aggressive.yaml` also moves to `divergent`.)

**Tests (`tests/unit/test_divergent_seed.py`):**

1. `test_divergent_strategy_yields_per_segment_seeds` — three segments → three different seeds.
2. `test_divergent_deterministic_for_same_run_seed` — same `(plan_hash, segment_idx, run_seed)` → same seed.
3. `test_divergent_different_segments_different_seeds` — segment 0 vs segment 1 → different seeds.
4. `test_per_run_unchanged_back_compat` — per_run still works as before (one seed for all).
5. `test_checkpoint_resume_replays_same_seeds` — resume → each segment gets same seed as initial run (state.json restoration).

## Workitem 4 — `audio.noise_overlay`

**Source:** Smitelli (2010) — `≥45%` white noise breaks CID; lower mixes
move chromaprint significantly without ruining intelligibility.

**File:** `src/yt_uniquifier/core/transforms/audio_noise_overlay.py`

```python
"""Parametric white/pink noise overlay mixed into the audio track.

Smitelli (2010) demonstrated that white noise overlay ≥45% (by amplitude
ratio) breaks YouTube CID audio matching. 45% is destructive — speech
intelligibility suffers. 20–30% shifts the chromaprint sub-fingerprints
significantly while keeping speech understandable on consumer playback.

Filter chain:
  [in]        original audio
  [n]         noise source (white/pink)
  [in][n]amix=inputs=2:weights=<1 - mix> <mix>:duration=first

Note: -10 dB on the noise side ≈ 30% amplitude mix.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

NoiseColor = Literal["white", "pink", "brown"]


class NoiseOverlayParams(BaseModel):
    # Noise level in dB relative to original. -10 dB ≈ 30% amplitude;
    # -6 dB ≈ 50% (destructive — Smitelli's threshold for breaking CID).
    noise_db: float = Field(default=-12.0, ge=-40.0, le=-3.0)
    color: NoiseColor = "pink"
    randomize_within_db: float = Field(default=0.0, ge=0.0, le=5.0)


def _build_noise_overlay(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, NoiseOverlayParams)
    noise_db = params.noise_db
    if params.randomize_within_db > 0 and rng is not None:
        from random import Random as _Random
        assert isinstance(rng, _Random)
        noise_db = max(
            -40.0,
            min(-3.0, noise_db + rng.uniform(
                -params.randomize_within_db, params.randomize_within_db,
            )),
        )
    # Convert dB to linear weight: 10**(dB/20)
    noise_weight = 10 ** (noise_db / 20.0)
    main_weight = 1.0 - noise_weight
    out = alloc.next("a")
    # anoisesrc with explicit color; we mix at amix with normalised weights.
    # Note: filter string is a chain — ffmpeg evaluates left-to-right.
    filt = (
        f"asplit[main][n_anchor];"
        f"[n_anchor]anoisesrc=c={params.color}:r=48000:amplitude=1[noise];"
        f"[main][noise]amix=inputs=2:weights={main_weight:.4f} {noise_weight:.4f}:"
        f"duration=first"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.noise_overlay",
        kind="audio",
        schema=NoiseOverlayParams,
        build=_build_noise_overlay,
        defaults={"noise_db": -12.0, "color": "pink", "randomize_within_db": 0.0},
    )
)
```

**Profile (`cid_aggressive.yaml` only, opt-in):**

```yaml
- id: audio.noise_overlay
  enabled: true
  params: {noise_db: -10.0, color: pink, randomize_within_db: 2.0}
```

**Not** in `cid_aware` because pink noise at -12 dB is audible on quiet
content. User explicitly opts in via `cid_aggressive`.

**Tests (`tests/unit/test_audio_noise_overlay.py`):**

1. `test_default_filter_shape` — `asplit[main][n_anchor];...amix=...weights=...`.
2. `test_db_to_weight_conversion` — `-12 dB` → weight ≈ 0.2512.
3. `test_color_param_propagates` — `color=brown` → `anoisesrc=c=brown`.
4. `test_randomize_within_db_seeded` — same seed → same noise_db.

## Acceptance

```bash
# 1. New transforms registered.
python -c "from yt_uniquifier.core.transforms import all_ids; \
  ids = all_ids(); \
  assert 'video.temporal_jitter' in ids; \
  assert 'audio.noise_overlay' in ids; \
  print('17 transforms total:', len(ids))"

# 2. cid_aware uses divergent strategy.
grep "seed_strategy" src/yt_uniquifier/profiles/cid_aware.yaml
# Expected: seed_strategy: divergent

# 3. cid_aware uses temporal_jitter.
grep "video.temporal_jitter" src/yt_uniquifier/profiles/cid_aware.yaml
# Expected: present, enabled: true

# 4. End-to-end on a multi-segment fixture.
yt-uniq run tests/fixtures/720.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out tests/fixtures/results/out_v033.mp4 \
  --encoder libx264 --workers 2 \
  --work-dir tests/fixtures/results/.v033_work

# 5. QA report shows new Hamming KPIs.
cat tests/fixtures/results/out_v033.mp4.qa.json | \
  jq '.audio_fp_hamming_per_frame, .audio_fp_match_confidence'

# 6. KPI target hit (real fixture):
#    pHash worst chunk < 0.80
python -c "
import json
qa = json.load(open('tests/fixtures/results/out_v033.mp4.qa.json'))
worst = max(c['similarity'] for c in qa.get('chunks', []))
print(f'worst chunk pHash: {worst}')
assert worst < 0.80, f'KPI miss: worst={worst}'
"
```

## Tests

| Уровень | Файл | Цель |
|---|---|---|
| Unit | tests/unit/test_temporal_jitter.py | 5 tests — filter shape, blur/no-blur, all-zero, bounds |
| Unit | tests/unit/test_audio_fp_hamming.py | 5 tests — identity, inversion, partial, unavailable, normalisation |
| Unit | tests/unit/test_divergent_seed.py | 5 tests — divergent strategy, determinism, segment differentiation, back-compat, resume |
| Unit | tests/unit/test_audio_noise_overlay.py | 4 tests — shape, dB conversion, color, jitter |
| Integration | tests/integration/test_v033_real_fixture.py | Run cid_aware on `tests/fixtures/720.mp4`; assert pHash worst chunk < 0.80, audio_fp_hamming_per_frame ≥ 15 |

## Risks

| Риск | Митигация |
|---|---|
| ffmpeg `random()` behaviour version-dependent | Unit tests snapshot the filter_str, not the runtime random output; integration test asserts only that run completes and KPIs are in range |
| temporal_jitter at default probs introduces visible flicker | Default blackout_blur=true uses gblur (smear, not flash); manual A/B listen test on real content recommended before tagging |
| Hamming distance computation O(N²) on long fingerprints | Pair frames 1:1 (input[i] ↔ output[i]) — linear; chromaprint emits ~7 frames/sec, 2h file = ~50k frames = tractable |
| Divergent seeds break resume because state.json doesn't store per-segment seeds | Add `segment_seeds: list[int]` to state.json schema; init_or_resume restores them; backward-compat: old state.json without this field → recompute |
| Cross-segment pHash divergence might make concat visually inconsistent | Test with manual A/B viewing; if jitter between segments is noticeable, ship divergent off-by-default in cid_aware and on-by-default only in cid_aggressive |
| noise_overlay at -10 dB audible on quiet content (silence, ASMR) | Default-off in cid_aware; only `cid_aggressive` enables it; documented in profiles.md |
| Total file duration shifts due to drop_prob (lossy) and dup_prob (additive) | Drop probability + dup probability roughly cancel at equal values; document in transform docstring; expect ±0.5% duration variation |

## Hand-off

After v0.3.3:

- 17 transforms in registry (was 14 after v0.3.2: + `video.temporal_jitter`,
  + `audio.noise_overlay`, + audio FP delta logic).
- `qa.json` now exposes audio Hamming KPI as a first-class field.
- `cid_aware` ships with all four academically-verified gaps closed.
- Real-fixture KPIs in target range: pHash worst chunk < 0.80, audio FP
  Hamming ≥ 15 bits/frame.
- Baseline ready for v0.4 work: LUT3D, platform profiles, real-CID
  validation harness, or ML-based attacks if KPIs still insufficient.

Tag: `v0.3.3`.

## Effort

| Workitem | Time |
|---|---|
| 1. `video.temporal_jitter` + 5 tests | 1 day |
| 2. Audio FP Hamming KPI + 5 tests + HTML wiring | 1 day |
| 3. Divergent seed strategy + 5 tests + resume support | 1 day |
| 4. `audio.noise_overlay` + 4 tests | 0.5 day |
| 5. Integration test + commit / push / tag | 0.5 day |
| **Total** | **4 working days** |
