# Spec 19 — Per-segment audio divergence (v0.4.2)

> **Phase 19 (v0.4.2)** · 1 day · **Deps:** v0.4.1 (real-CID validation
> harness should produce ≥ 5 samples first so we know if this is needed)

## Context

`seed_strategy: divergent` (v0.3.3 / Spec 16) gives each *video* segment
a distinct seed derived from `sha256(plan_hash, segment_idx, run_seed)`.
But `core/pipeline.py::build_main_audio_command` runs **once** on the
full source — this was a v0.1 design decision to avoid loudnorm transient
artefacts at segment seams, and it predates divergent seeds.

The consequence: audio transforms (rubberband pitch, Haas delay, compand,
EQ band gain, spectral smear chorus depth) get **one** rng draw per run.
Every segment shares the same pitch shift, same Haas delay, same EQ
profile. A temporal-aware audio CID has a stable target: it can sample
any one window and match against the run-wide parameters.

This phase fixes that asymmetry. We split the audio chain into N
windows of ~60 s each, give each window its own seed (derived the same
way as video segments), and crossfade at the seams. Loudnorm stays
global — its two-pass measurement still runs on the full source.

The work is non-trivial: it touches the audio chain build, segmenter's
audio path, the loudnorm coordination, and adds crossfade math. v0.4.1
ships first so we have empirical data about whether v0.4.0's audio
strengthening already hits target. If yes, this phase becomes optional.

## Goal

After v0.4.2:

- When `seed_strategy: divergent`, audio is processed as N windows with
  distinct per-window seeds; rubberband pitch / Haas delay / compand
  threshold / EQ bands all vary across windows.
- Adjacent windows crossfade over 0.1 s via `acrossfade`. No audible seam.
- Loudnorm two-pass measurement still runs once on the full source; final
  loudnorm-apply pass runs on the concatenated window output.
- Audio FP Hamming KPI (`audio_fp_hamming_per_frame`) increases from ≥ 15
  bits (v0.4.0) to ≥ 18 bits.
- New KPI: `audio_fp_hamming_variance_between_windows ≥ 4 bits` — adjacent
  windows look meaningfully different.
- 365 v0.3.3 + ~15 v0.4.0 + ~15 new tests stay green.
- Tag: `v0.4.2`.

## Scope

**In:**

- Window-split audio processing under `seed_strategy: divergent`.
- Per-window seed derivation via existing
  `seed_resolver.derive_segment_seed` (same function, new namespace).
- `acrossfade=d=0.1` between windows.
- Two-pass loudnorm preserved globally.
- New `audio_fp_variance_between_windows` field in QAReport.
- Updated QA HTML to show per-window audio Hamming heatmap.

**Not in:**

- Changing behaviour for non-divergent strategies (`fixed`, `per_run`,
  `per_file`) — those keep the existing single-pass audio chain.
- Variable window size — fixed 60 s windows in this release. Adaptive
  windowing (e.g. on scene changes) is v0.5.
- Per-window loudnorm measurement — loudnorm stays single-pass on full
  source.
- Cancelling the per-segment video work; this is purely additive.

## Architecture

```
v0.3.3 audio flow (single pass):
  full source → [pitch, eq, haas, compand, loudnorm-apply] → main_audio.m4a

v0.4.2 audio flow under seed_strategy=divergent:
  full source ─┬─► [window_1] pitch_1, eq_1, haas_1, compand_1 ──┐
               ├─► [window_2] pitch_2, eq_2, haas_2, compand_2 ──┤
               ├─► [window_3] pitch_3, eq_3, haas_3, compand_3 ──┼─► acrossfade chain ─► loudnorm-apply ─► main_audio.m4a
               └─► [window_N] pitch_N, eq_N, haas_N, compand_N ──┘
  loudnorm-measure runs once on raw full source (unchanged).
```

The window seeds come from the same `derive_segment_seed(plan_hash, idx,
run_seed)` helper used for video — under divergent strategy, audio
window `i` and video segment `i` derive different seeds because their
indices don't overlap (we namespace audio windows as
`f"audio_window:{i}"` so even if the index numerically coincides, the
hash inputs differ).

## Workitem 1 — Window planner

**File:** `src/yt_uniquifier/core/audio_windows.py` (new)

```python
"""Plan audio processing windows for the divergent seed strategy.

Each window gets ~60 s of audio + 0.1 s overlap on each side for the
acrossfade between adjacent windows. The first window starts at 0 with
no leading overlap; the last ends at duration with no trailing overlap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioWindow:
    idx: int
    start_sec: float
    end_sec: float
    crossfade_in_sec: float   # 0 for the first window
    crossfade_out_sec: float  # 0 for the last window


WINDOW_SEC = 60.0
CROSSFADE_SEC = 0.1


def plan_windows(duration_sec: float) -> list[AudioWindow]:
    """Split [0, duration_sec] into ≈ duration_sec / WINDOW_SEC pieces.

    For short audio (< 2 × WINDOW_SEC), returns a single window covering
    the whole duration — no point splitting and crossfading 30 s of audio.
    """
    if duration_sec <= 2 * WINDOW_SEC:
        return [AudioWindow(idx=0, start_sec=0.0, end_sec=duration_sec,
                            crossfade_in_sec=0.0, crossfade_out_sec=0.0)]

    windows: list[AudioWindow] = []
    n_windows = int(duration_sec / WINDOW_SEC)
    # Distribute evenly; last window absorbs the remainder.
    base = duration_sec / n_windows
    for i in range(n_windows):
        start = i * base
        end = (i + 1) * base if i < n_windows - 1 else duration_sec
        cf_in = CROSSFADE_SEC if i > 0 else 0.0
        cf_out = CROSSFADE_SEC if i < n_windows - 1 else 0.0
        windows.append(AudioWindow(
            idx=i, start_sec=start, end_sec=end,
            crossfade_in_sec=cf_in, crossfade_out_sec=cf_out,
        ))
    return windows
```

**Tests:** `tests/unit/test_audio_windows.py`

```python
def test_short_audio_one_window():
    windows = plan_windows(45.0)
    assert len(windows) == 1
    assert windows[0].start_sec == 0.0
    assert windows[0].end_sec == 45.0
    assert windows[0].crossfade_in_sec == 0.0
    assert windows[0].crossfade_out_sec == 0.0

def test_2min_audio_two_windows():
    windows = plan_windows(120.0)
    assert len(windows) == 2
    assert windows[0].crossfade_in_sec == 0.0
    assert windows[0].crossfade_out_sec == 0.1
    assert windows[1].crossfade_in_sec == 0.1
    assert windows[1].crossfade_out_sec == 0.0

def test_5min_audio_five_windows_no_gaps():
    windows = plan_windows(300.0)
    assert len(windows) == 5
    # Adjacent windows must be contiguous (last.end == next.start).
    for a, b in zip(windows, windows[1:], strict=True):
        assert abs(a.end_sec - b.start_sec) < 1e-6

def test_last_window_absorbs_remainder():
    """A 130 s clip should produce 2 windows: 65s + 65s, not 60+60+10."""
    windows = plan_windows(130.0)
    assert len(windows) == 2
    assert windows[-1].end_sec == 130.0
```

## Workitem 2 — Pipeline-level: build per-window commands

**File:** `src/yt_uniquifier/core/pipeline.py`

Add a new function `build_main_audio_command_windowed` that:
1. Calls `plan_windows(source.duration_sec)`.
2. For each window, derives a seed via
   `derive_segment_seed(plan.plan_hash, idx, plan.run_seed)` (with idx
   offset to avoid collision with video segment indices).
3. Builds a per-window filter chain that:
   - `atrim=start={w.start - w.crossfade_in}:end={w.end + w.crossfade_out}`
   - applies audio transforms (pitch, eq, haas, compand, spectral_smear,
     reverb, noise_overlay) with the per-window seed
   - emits a labeled output `[aw_{idx}]`
4. Concatenates the window outputs via `acrossfade` chains.
5. Applies global loudnorm to the concatenated stream (pre-measured).

```python
AUDIO_WINDOW_NS_OFFSET = 1_000_000  # offsets audio window indices vs video segment indices

def build_main_audio_command_windowed(
    plan: Plan,
    audio_output: Path,
    *,
    loudnorm_measurement: LoudnormMeasurement | None = None,
) -> tuple[BuiltCommand, LoudnormMeasurement | None]:
    """Per-window audio processing under divergent seed strategy.

    Same return shape as build_main_audio_command, so callers
    (segmenter.process_main_audio) can swap between the two.
    """
    from yt_uniquifier.core.audio_windows import plan_windows
    from yt_uniquifier.core.seed_resolver import derive_segment_seed

    alloc = LabelAllocator()
    audio_transforms = [
        tc for tc in plan.profile.transforms
        if tc.enabled and get(tc.id).kind == "audio" and tc.id != LOUDNORM_ID
    ]
    if not audio_transforms or not plan.source.audio:
        return (BuiltCommand(args=[], ...), loudnorm_measurement)

    # 1. Optional loudnorm measure pass (unchanged from non-windowed path).
    measurement = loudnorm_measurement
    needs_loudnorm = any(
        tc.id == LOUDNORM_ID for tc in plan.profile.transforms if tc.enabled
    )
    if needs_loudnorm and measurement is None:
        ln_params = _loudnorm_params_from(plan.profile.transforms)
        measurement = measure(plan.source.path, ln_params)

    # 2. Plan windows.
    windows = plan_windows(plan.source.duration_sec)

    # 3. Per-window chain. Each window is an asplit + atrim + transforms.
    window_chains: list[str] = []
    window_labels: list[str] = []
    for w in windows:
        # Derive a per-window seed via the same hash as video segments,
        # offset by AUDIO_WINDOW_NS_OFFSET so audio idx 0 ≠ video segment 0.
        seed = derive_segment_seed(
            plan.plan_hash, w.idx + AUDIO_WINDOW_NS_OFFSET, plan.run_seed,
        )
        window_rng = random.Random(seed)

        # asplit + atrim to isolate this window.
        cut_in = max(0.0, w.start_sec - w.crossfade_in_sec)
        cut_out = min(plan.source.duration_sec, w.end_sec + w.crossfade_out_sec)
        win_in = alloc.next("a")
        # Apply transforms with this window's seeded rng.
        a_label = win_in
        win_chain: list[str] = [f"atrim=start={cut_in:.4f}:end={cut_out:.4f},asetpts=PTS-STARTPTS"]
        for tc in audio_transforms:
            spec = get(tc.id)
            params = spec.schema.model_validate({**spec.defaults, **tc.params})
            chain = call_build(spec, params, alloc, a_label, rng=window_rng)
            win_chain.append(chain.filter_str)
            a_label = chain.out_label
        # Compose the window chain. Input is [0:a:0]; output label for the
        # window is the last allocated label.
        window_labels.append(a_label)
        window_chains.append(
            f"[0:a:0]{','.join(win_chain)}"
            if False else  # see note below
            f"[0:a:0]" + ",".join(win_chain) + f"[{a_label}]"
        )
        # Above is a simplification — the real implementation interleaves
        # per-transform [in]→[out] labels per existing pipeline pattern.
        # See worked example in next workitem.

    # 4. Crossfade adjacent windows.
    # Pairwise reduce: [aw_0] ◊ [aw_1] → [acf_0_1]; [acf_0_1] ◊ [aw_2] → [acf_2]; …
    acrossfade_chains: list[str] = []
    accumulator = window_labels[0]
    for i, next_label in enumerate(window_labels[1:], start=1):
        out_label = alloc.next("a")
        acrossfade_chains.append(
            f"[{accumulator}][{next_label}]"
            f"acrossfade=d={CROSSFADE_SEC}:c1=tri:c2=tri[{out_label}]"
        )
        accumulator = out_label

    # 5. Apply global loudnorm to the concatenated stream.
    if needs_loudnorm:
        assert measurement is not None
        ln_params = _loudnorm_params_from(plan.profile.transforms)
        rng = random.Random(plan.run_seed)
        ln_chain = build_apply(ln_params, measurement, alloc, accumulator, rng=rng)
        final_label = ln_chain.out_label
        loudnorm_str = f"[{ln_chain.in_label}]{ln_chain.filter_str}[{ln_chain.out_label}]"
    else:
        final_label = accumulator
        loudnorm_str = ""

    filter_complex = ";".join(
        window_chains + acrossfade_chains + ([loudnorm_str] if loudnorm_str else [])
    )

    args = [
        ffmpeg_bin(), "-hide_banner", "-y",
        "-i", str(plan.source.path), "-vn",
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-c:a", "aac", "-b:a", "256k",
        "-map_metadata", "-1",
        str(audio_output),
    ]
    return (
        BuiltCommand(
            args=args, filter_complex=filter_complex,
            output_video_label="", output_audio_label=final_label,
            loudnorm_measurement=measurement,
        ),
        measurement,
    )
```

**Note on label management:** the simplified pseudo-chain `[0:a:0]F1,F2,F3
[a_out]` works for a single straight-through pipeline. For our case each
transform allocates its own intermediate label, so the real
implementation must emit them sequentially (same pattern as existing
`build_main_audio_command`). The above is a sketch; the implementation
follows the existing audio-chain assembly idiom verbatim — `for tc in
audio_transforms: append [label_n]filter[label_n+1]`.

## Workitem 3 — Segmenter integration

**File:** `src/yt_uniquifier/core/segmenter.py`

Add a thin dispatcher in `process_main_audio`:

```python
def process_main_audio(
    plan: Plan, work_dir: Path,
    *, loudnorm_measurement: LoudnormMeasurement | None = None,
    on_event: ...
) -> tuple[Path | None, LoudnormMeasurement | None]:
    out = work_dir / "main_audio.m4a"
    if plan.profile.seed_strategy == "divergent":
        from yt_uniquifier.core.pipeline import build_main_audio_command_windowed
        cmd, measurement = build_main_audio_command_windowed(
            plan, out, loudnorm_measurement=loudnorm_measurement,
        )
    else:
        cmd, measurement = build_main_audio_command(
            plan, out, loudnorm_measurement=loudnorm_measurement,
        )
    if not cmd.args:
        return None, measurement
    run_ffmpeg(cmd, output=out, on_event=on_event, ...)
    return out, measurement
```

## Workitem 4 — QA: per-window Hamming variance

**File:** `src/yt_uniquifier/core/qa/audio_fp.py`

Add a function that computes Hamming per ~30 s window of audio (chromaprint
output, sliced into N equal windows of subfingerprints) and returns the
variance between adjacent windows:

```python
@dataclass(frozen=True)
class AudioFPVariance:
    available: bool
    hamming_per_window: list[float] | None
    variance_between_windows: float | None  # std of pairwise deltas
    note: str | None = None


def compare_hamming_per_window(
    input_path: Path, output_path: Path, n_windows: int = 5,
) -> AudioFPVariance:
    """Split paired chromaprint streams into n_windows equal windows.

    For each window, compute mean Hamming distance per frame. Return the
    standard deviation of those window means as a measure of "how much
    does our audio vary across the timeline".

    With v0.3.3 (uniform audio): variance ≈ 0 (all windows identical).
    With v0.4.2 (windowed audio): variance ≥ 4 bits expected.
    """
    ...
```

**File:** `src/yt_uniquifier/core/models.py` — extend QAReport:

```python
class QAReport(BaseModel):
    # ...existing v0.3.3 fields...
    audio_fp_hamming_variance: float | None = None
    audio_fp_hamming_per_window: list[float] | None = None
```

**File:** `src/yt_uniquifier/core/qa/report.py` — wire into `build_report`.

**HTML template:** add a per-window audio Hamming heatmap (5–10 cells,
colored by per-window Hamming distance).

## Workitem 5 — Tests

| File | Tests |
|---|---|
| `tests/unit/test_audio_windows.py` | 4 tests — short audio single window, 2min two windows, 5min five windows no gaps, last window absorbs remainder |
| `tests/unit/test_main_audio_windowed.py` | 6 tests — divergent triggers windowed path, non-divergent stays on legacy path, per-window seeds differ, loudnorm uses global measure, crossfade syntax present, single-window degenerate case |
| `tests/unit/test_audio_fp_variance.py` | 4 tests — identical input/output → variance 0, divergent fixture → variance ≥ 4 bits, single-window degenerate, fpcalc-missing graceful skip |
| `tests/integration/test_audio_divergence_real_ffmpeg.py` | 1 test — run cid_aware on a 90s testsrc2+sine fixture, verify ffmpeg accepts the filter_complex, output has expected duration ± 0.1s |

Total: ~15 new tests.

## Acceptance

```bash
# 1. divergent profile triggers windowed audio path.
.venv/bin/python -c "
from yt_uniquifier.core.profile_loader import load_profile
from pathlib import Path
p = load_profile(Path('src/yt_uniquifier/profiles/cid_aware.yaml'))
assert p.seed_strategy == 'divergent'
"

# 2. End-to-end on a 90s real fixture.
yt-uniq run /path/to/90s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out_v042.mp4 --encoder libx264

# 3. QA report shows the new KPI.
cat out_v042.mp4.qa.json | jq '.audio_fp_hamming_variance, .audio_fp_hamming_per_window'
# Expected: variance ≥ 4.0, list of 5 floats not all equal.

# 4. Audible A/B against v0.4.0 baseline.
# Manual: open both in headphones; the windowed version should not have
# audible seams at the 60-sec marks.

# 5. All tests + lint.
pytest -q
ruff check .
mypy src/yt_uniquifier
```

## Risks

| Риск | Митигация |
|---|---|
| acrossfade seams audible on continuous-tone content (sine sweep, organ music) | 0.1 s with `c1=tri:c2=tri` is below human perception of cross-fade for non-tonal audio; for tonal content user can disable divergent strategy or shorten crossfade to 0.05s |
| rubberband pitch shift discontinuity at window boundary (small but real) | acrossfade smooths the discontinuity; if KPI floor (variance ≥4) requires very different pitch per window, pitch jitter range may be too narrow to avoid audible jumps — tune `pitch_tempo.randomize_within` if needed |
| Per-window loudness vs global loudnorm interplay | Loudnorm is applied **after** the windowed concatenation, on the full stream — so the global target is preserved. Per-window levels may have minor variance but loudnorm flattens it |
| Filter graph size grows ~Nx (where N = windows) | For a 2h source: N ≈ 120 windows, filter_complex grows from ~2 KB to ~50 KB. Within ffmpeg's limits (it handles MB-sized filtergraphs); verify by smoke-testing a 2h fixture |
| `acrossfade` only works on pairs — chaining N requires N-1 nested filter applications | Implementation chains pairwise: `aw_0 + aw_1 → acf_1; acf_1 + aw_2 → acf_2; …`. Linear in N. Documented in the code |
| Spec depends on v0.4.1 sample data, but harness only produces 5 samples on day 1 | If v0.4.1 shows v0.4.0 already hits real-CID KPI, this spec is **deferrable** — re-evaluate after week 2 of validation runs |

## Hand-off

After v0.4.2:

- Under `seed_strategy: divergent`, audio is no longer uniform across
  segments — each ~60 s window has its own pitch / Haas / EQ params,
  joined by 0.1 s tri crossfades.
- New `audio_fp_hamming_variance` KPI in `qa.json` measures how
  meaningfully the audio varies across the timeline.
- Loudnorm semantics unchanged (still single global target, two-pass).
- Behaviour for non-divergent strategies is unchanged.
- 21 transforms (no new transforms added) + new window-planning module.

Tag: `v0.4.2`.

## Effort

| Item | Time |
|---|---|
| 1. `audio_windows.py` + 4 tests | 1 hour |
| 2. `build_main_audio_command_windowed` (with proper label management) | 3 hours |
| 3. Segmenter dispatcher | 30 min |
| 4. `compare_hamming_per_window` + QAReport fields + HTML | 1.5 hours |
| 5. Tests (~11 new across 4 files) | 1.5 hours |
| 6. Real-fixture validation (90s + 5min A/B) | 1 hour |
| 7. Lint, type-check, commit, tag | 30 min |
| **Total** | **~9 hours / 1 day** |
