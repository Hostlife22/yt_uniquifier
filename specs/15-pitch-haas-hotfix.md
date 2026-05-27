# Spec 15 — Pitch threshold fix + Haas stereo (v0.3.2 hotfix)

> **Phase 15 (v0.3.2)** · 1 day · **Deps:** v0.3.1

## Context

Research synthesised in [v0.3.2-3-plan.md](./v0.3.2-3-plan.md) revealed
that our shipped `cid_aware.yaml` profile uses `audio.pitch_tempo.pitch =
1.04` — a +4% shift. Scott Smitelli's 2010 controlled experiment against
YouTube Content ID established:

- pitch shift `≤ ±5%` → CID **matches** (we lose)
- pitch shift `≥ ±6%` → CID **does not match** (we win)

Our default sits inside the match zone. This is a config-only fix: one
YAML literal. Bundled with it is a small new transform (`audio.haas_stereo`)
that adds another verified evasion vector (Smitelli showed phase
manipulation breaks audio CID; Haas is its mono-compatible variant) and a
documentation update so future maintainers can verify the parameter choice
against its source.

## Goal

After v0.3.2:

- `cid_aware.yaml` ships `pitch=1.06` (above Smitelli threshold).
- `cid_aggressive.yaml` ships `pitch=1.08` (well above).
- New transform `audio.haas_stereo` is registered and enabled in
  `cid_aware` (subtle, mono-compatible).
- `docs/profiles.md` documents pitch & Haas choices with explicit citations
  to Smitelli (2010), so the parameter rationale is auditable.

## Scope

**In:**

1. Bump `pitch` literal in two YAML profiles.
2. New transform `audio.haas_stereo` (ffmpeg `adelay` + `pan` /
   `stereotools`).
3. Documentation update.
4. Two small unit tests + one integration smoke.

**Not in:** temporal jitter, audio FP delta KPI, divergent seed strategy,
noise overlay — these are v0.3.3 ([16-temporal-jitter-and-divergence.md](./16-temporal-jitter-and-divergence.md)).

## Workitem 1 — Bump pitch defaults

**Files:**

- `src/yt_uniquifier/profiles/cid_aware.yaml`

  ```yaml
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.06, randomize_within: 0.005, method: rubberband}
  ```

- `src/yt_uniquifier/profiles/cid_aggressive.yaml`

  ```yaml
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.08, tempo: 0.99, randomize_within: 0.01, method: rubberband}
  ```

**Rationale (kept in docs/profiles.md):** Smitelli 2010 verified +5% still
matches; +6% does not. We pick `1.06` for cid_aware (just past the
threshold) and `1.08` for cid_aggressive (comfortable margin).
`randomize_within` ensures that even with seed jitter the lower bound stays
above 1.055 in cid_aware. The `rubberband` method preserves formants so
voice stays natural at this shift.

**Test:**

- Existing profile-load tests pick up the new values automatically.
- Add `tests/unit/test_profile_pitch_threshold.py` with one assertion per
  profile: loaded `pitch` >= 1.06 for cid_aware, >= 1.08 for cid_aggressive.
  This guards against accidental regression.

## Workitem 2 — `audio.haas_stereo` transform

**File:** `src/yt_uniquifier/core/transforms/audio_haas.py` (new)

```python
"""Haas effect — delay one stereo channel by a few milliseconds.

Smitelli (2010) showed that stereo phase inversion breaks YouTube CID
audio fingerprinting. Full inversion sounds unnatural. Haas (the
"precedence effect" in psychoacoustics) is its mono-compatible cousin:
a 5–30 ms delay on one channel is imperceptible on a mono mix, sounds
like a slight stereo widening on a stereo mix, and shifts the cross-channel
phase relationship that the fingerprinter relies on.

Source: Smitelli, "Fun with YouTube's Audio Content ID System" (2010).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)


class HaasStereoParams(BaseModel):
    # Delay in milliseconds on the right channel. 5–30 ms stays in Haas
    # range — perceptually fused as a single source, not heard as echo.
    delay_ms: float = Field(default=15.0, ge=1.0, le=40.0)
    # Per-run jitter ±N ms around delay_ms (with rng).
    randomize_within_ms: float = Field(default=0.0, ge=0.0, le=10.0)


def _build_haas(
    params: BaseModel, alloc: LabelAllocator, in_lbl: str, *, rng: object = None
) -> FilterChain:
    assert isinstance(params, HaasStereoParams)
    delay = params.delay_ms
    if params.randomize_within_ms > 0 and rng is not None:
        from random import Random as _Random
        assert isinstance(rng, _Random)
        delay = max(
            1.0,
            min(40.0, delay + rng.uniform(
                -params.randomize_within_ms, params.randomize_within_ms,
            )),
        )
    out = alloc.next("a")
    # Delay only the right channel (0 ms on left, N ms on right).
    # adelay accepts a per-channel list; "0|N" leaves left untouched.
    filt = f"adelay={0}|{int(round(delay))}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(
    TransformSpec(
        id="audio.haas_stereo",
        kind="audio",
        schema=HaasStereoParams,
        build=_build_haas,
        defaults={"delay_ms": 15.0, "randomize_within_ms": 0.0},
    )
)
```

**Registry wiring:** add `audio_haas` to `core/transforms/__init__.py`
imports + `__all__`.

**Profile integration (`cid_aware.yaml`):** add between `audio.eq` and
`audio.resample`:

```yaml
- id: audio.haas_stereo
  enabled: true
  params: {delay_ms: 15.0, randomize_within_ms: 4.0}
```

`cid_aggressive.yaml`: same but `delay_ms: 25.0`, `randomize_within_ms: 8.0`.

**Tests (`tests/unit/test_transform_haas.py`):**

1. `test_default_filter_shape` — `adelay=0|15`.
2. `test_delay_param_propagates` — `delay_ms=25` → `adelay=0|25`.
3. `test_randomize_within_seeded_reproducible` — same seed → same delay.
4. `test_randomize_within_clamped_to_range` — extreme rng values stay in
   [1, 40] ms.

## Workitem 3 — Documentation update

**File:** `docs/profiles.md` — append new section:

```markdown
### Why these defaults? — Smitelli citation

The `cid_aware` and `cid_aggressive` profiles target YouTube Content ID
audio matching thresholds documented in Scott Smitelli's 2010 controlled
experiment ("Fun with YouTube's Audio Content ID System",
https://www.scottsmitelli.com/articles/youtube-audio-content-id/).

Verified thresholds:

| Transform | CID matches | CID does not match |
|---|---|---|
| pitch shift | `\| ±5% \|` (4% in v0.3.1 was in match zone) | `\| ±6% \|` |
| white noise overlay | `< 45%` mix | `≥ 45%` mix |
| stereo phase | identity | full inversion |

v0.3.2 sets:
- `cid_aware.pitch = 1.06` (just past 5% threshold)
- `cid_aggressive.pitch = 1.08` (safe margin)
- `audio.haas_stereo` with delay 15–30 ms (mono-compatible variant of
  phase inversion)

These are not guarantees, only verified historical thresholds. YouTube
CID has been updated since 2010; community reports suggest the thresholds
are still in approximately the same ranges, but the only authoritative
test is a real upload against your own corpus.
```

**Also update:** `README.md` — bump "13 transforms" to "14 transforms" and
mention the v0.3.2 release in the Status section.

## Acceptance

```bash
# 1. Profile bumps land.
grep -E "pitch:" src/yt_uniquifier/profiles/cid_aware.yaml
# Expected: pitch: 1.06
grep -E "pitch:" src/yt_uniquifier/profiles/cid_aggressive.yaml
# Expected: pitch: 1.08

# 2. New transform is in registry.
yt-uniq probe --encoders > /dev/null   # warm cache
python -c "from yt_uniquifier.core.transforms import all_ids; print('audio.haas_stereo' in all_ids())"
# True

# 3. Real-fixture smoke (audio side only — pitch + haas applied).
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out tests/fixtures/results/out_v032.mp4 \
  --encoder libx264 --work-dir tests/fixtures/results/.v032_work \
  --no-qa --no-progress

# 4. Inspect output filter graph (look for adelay + asetrate or rubberband).
yt-uniq probe tests/fixtures/results/out_v032.mp4 | jq '.audio[0]'
# Should show standard audio stream — no errors during transformation.

# 5. All existing 330 tests pass.
pytest -q

# 6. New tests pass.
pytest -q tests/unit/test_transform_haas.py tests/unit/test_profile_pitch_threshold.py
```

## Tests

| Уровень | Файл | Цель |
|---|---|---|
| Unit | `tests/unit/test_profile_pitch_threshold.py` | cid_aware.pitch ≥ 1.06; cid_aggressive.pitch ≥ 1.08 (regression guard) |
| Unit | `tests/unit/test_transform_haas.py` | 4 tests — default shape, param propagation, seeded jitter, range clamp |

## Risks

| Риск | Митигация |
|---|---|
| Haas 25+ ms заметен как echo на headphones-mono mix | cid_aware default 15 ms — well inside fusion threshold; cid_aggressive at 25 ms is still safe per psychoacoustic literature |
| Rubberband pitch 1.06 на vocal content слышно как «лёгкое повышение тона» | Acceptable trade-off — Smitelli verified that 5% is in match zone; user controls via `--method asetrate` fallback |
| ffmpeg `adelay` syntax variants across versions | Use `0|N` channel-list form — supported since ffmpeg 3.x; we already require modern ffmpeg for rubberband (4+) |
| Existing pHash similarity smoke regression test breaks | Pitch change is audio-only; pHash is video — no effect expected; if it breaks, investigate (shouldn't happen) |

## Hand-off

After v0.3.2:

- Default profile audio path passes Smitelli's documented CID thresholds.
- 14 transforms in registry (was 13: + `audio.haas_stereo`).
- `docs/profiles.md` carries an auditable source for parameter choices.
- Baseline ready for v0.3.3 to layer temporal jitter + divergent seeds +
  noise overlay onto.

Tag: `v0.3.2`.

## Effort

| Item | Time |
|---|---|
| 1. Profile bumps + regression test | 15 min |
| 2. `audio.haas_stereo` module + 4 tests | 4 hours |
| 3. Docs update + README bump | 1 hour |
| 4. CI / commit / push / tag | 30 min |
| **Total** | **~6 hours / 1 working day** |
