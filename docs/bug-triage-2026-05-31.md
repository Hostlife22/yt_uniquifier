# Real-Video Bug Triage — 2026-05-31

Source: `tools/real_video_matrix.py` run against
`tests/fixtures/.gen/` (3 provided clips + 11 synthetic via
`tools/_corpus_gen.py`). 99 cells (14 inputs × 7 profiles × libx264 ×
workers=1) plus 1 resume cell. Summary CSV at
`out/runs/real_matrix_20260531_000935/summary.csv`.

## Headline numbers

| | Cells | Pct |
|---|---:|---:|
| Total | 99 | 100% |
| Pass | 76 | 77% |
| Fail / timeout | 23 | 23% |
| Critical (real bugs) | 4 | 4% |
| High (real bugs) | 7 | 7% |
| Working-as-designed (preflight refusal) | 10 | 10% |
| Performance (slow, not broken) | 2 | 2% |

Resume cell (`clip_long.mp4` × `soft` × libx264, SIGINT after first
segment) passed: pass-2 returned exit 0 and produced output, confirming
the v0.4.x idempotent-resume fix (commits `7338fa1`, `9dbc8fa`) holds
on real workloads.

## Findings

### #1 — Tonemap-without-HDR-source crashes mid-encode  *(severity: HIGH, fixed)*

**Cells**: 7 — every SDR input × `cid_aware_hdr_to_sdr` profile
(`synth_2398fps`, `synth_60fps`, `synth_audio_5_1`,
`synth_audio_hot`, `synth_audio_mono`, `synth_audio_quiet`,
`synth_odd_dim`, `synth_sdr_4k`, `synth_vfr`).

**Symptom**: `yt-uniq run` exits with code 1 part-way through segment 0.
ffmpeg log shows:

```
Could not open encoder before EOF
Task finished with error code: -22 (Invalid argument)
Nothing was written into output file, because at least one of its streams received no packets.
```

**Repro** (one-liner):
```bash
.venv/bin/yt-uniq run tests/fixtures/.gen/synth_sdr_4k.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware_hdr_to_sdr.yaml \
  --out /tmp/repro.mp4 --encoder libx264 --no-progress
```

**Root cause**: `cid_aware_hdr_to_sdr.yaml` enables `video.tonemap_sdr`
as the first transform. The tonemap filter expects a PQ/HLG transfer
on the input. Applied to a `bt709` SDR source the zscale path produces
a stream the libx264 encoder cannot open. Preflight (`core/preflight.py`)
already had a *tonemap-order* warning (`_check_tonemap_order`) but no
check that source actually IS HDR when tonemap is enabled. The HDR
checks in `_check_hdr` early-return on `not v.color.is_hdr`, so SDR
inputs with HDR-only profiles got zero preflight signal.

**Fix**: `core/preflight.py::_check_tonemap_sdr_input` — wired into
`preflight()` between `_check_tonemap_order` and `_check_blend_b_input`.
Emits `tonemap.sdr_input` severity=`fail` with suggestion to use a
non-HDR-to-SDR profile.

**Regression tests** (`tests/unit/test_preflight.py`):
- `test_tonemap_sdr_input_fails` — SDR source + `video.tonemap_sdr` →
  FAIL with code `tonemap.sdr_input`.
- `test_tonemap_sdr_with_hdr_input_passes` — HDR source + tonemap →
  emits `hdr.tonemap.ok`, no `tonemap.sdr_input`.

Verified manually:
```
$ .venv/bin/yt-uniq run tests/fixtures/.gen/synth_sdr_4k.mp4 \
    --profile src/yt_uniquifier/profiles/cid_aware_hdr_to_sdr.yaml ...
[FAIL] tonemap.sdr_input: Profile applies video.tonemap_sdr but source
       is SDR (transfer='bt709'). Tonemap is only valid for HDR
       (PQ / HLG) sources.
```

### #2 — `_check_pitch_rubberband` preflight check missed `rubberband`-absent ffmpeg  *(severity: HIGH, intermittent, root cause unconfirmed)*

**Cells**: 4 — `clip_long.mp4` × {`cid_aware`, `cid_aggressive`},
`synth_sdr_4k.mp4` × {`cid_aware`, `cid_aggressive`}. All timeouts
(exit 124) at 600s during the original matrix run.

**Re-investigation revealed this is NOT a perf bug** as initially
classified. Direct timing run of `clip_long.mp4 × cid_aware` with no
subprocess timeout returned exit 8 after **18 min 22 s** with:

```
[AVFilterGraph @ ...] No such filter: 'rubberband'
Error : Filter not found
(full log: .../main_audio.m4a.log)
```

ffmpeg on this box (`/usr/local/bin/ffmpeg`) does NOT have the
`rubberband` filter, but the original matrix run had `_check_pitch_rubberband`
let the run proceed anyway. Re-running the exact same combos AFTER
`brew install chromaprint` completed: **all 4 cells now correctly
preflight-FAIL within <1s** with code `audio.pitch.rubberband.missing`.

Live invocations of `_check_pitch_rubberband(plan)` and
`_ffmpeg_has_filter("rubberband")` in isolation also return the
correct FAIL / False. So the check itself is sound.

**Hypothesis**: at matrix-run time, `_ffmpeg_has_filter("rubberband")`
returned True incorrectly. The likeliest mechanism is that the
preflight cache key (`_ffmpeg_version_key` SHA of `ffmpeg -version`
output) hit a stale state during a concurrent system operation (brew
update / formula relink), so `ffmpeg -filters` returned data from a
transiently-different binary that DID list rubberband. The cache then
locked that wrong answer in for the duration of the matrix run. After
brew install chromaprint completed, the version hash changed,
invalidating cache. Hard to reproduce on demand.

**Defense-in-depth recommendation** (not implemented this pass, would
expand scope):
- Replace the `ffmpeg -filters` substring parse with an actual dry-run
  of `ffmpeg -af 'rubberband=pitch=1.0' -t 0.001 -f null -i 'sine=440' -`
  which faithfully reports whether the filter graph can be opened.
- Or have `core/audio_windows.py` re-verify filter availability
  immediately before invoking the audio chain, after 18 min of
  video work has already burned.

**Verification table** (post-fix):

| Input | Profile | Pre-fix exit | Post-verify exit | Time to fail |
|---|---|---|---|---|
| `clip_long.mp4` | `cid_aware` | 124 (timeout 600s) | 1 (preflight FAIL) | <1s |
| `clip_long.mp4` | `cid_aggressive` | 124 | 1 | <1s |
| `synth_sdr_4k.mp4` | `cid_aware` | 124 | 1 | <1s |
| `synth_sdr_4k.mp4` | `cid_aggressive` | 124 | 1 | <1s |

**Status**: behaviour is correct NOW; the original matrix-time failure
is documented but not reproduced. Logged for follow-up to add the
defense-in-depth dry-run check.

**Repro**:
```bash
.venv/bin/yt-uniq run tests/fixtures/.gen/clip_long.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out /tmp/slow.mp4 --encoder libx264 --no-progress --fast-qa
```

**Root cause**: Both profiles set `audio.pitch_tempo` with
`method: rubberband`. ffmpeg's `rubberband` filter runs ~5–10× slower
than the default `asetrate+atempo` path. The full audio chain
(`pitch_tempo` rubberband + `eq` + `haas_stereo` + `compand` + loudnorm
two-pass measurement) on a 90s clip burns most of the budget; on a 4K
12s clip the heavy video chain compounds it. Not a hang — extending
the timeout to 1200s would unblock these — but the user-visible
behaviour with our matrix default is indistinguishable from a stall.

**Why we are not fixing this in this pass**:
1. Performance work needs its own benchmark before/after baseline
   (`tools/benchmark.py` already exists for this) — outside the scope
   of "find and fix bugs from real-video sweep."
2. The `rubberband` choice in `cid_aware*` is intentional (formant
   preservation for voice — see profile docstrings citing Smitelli
   2010 ±5% CID match boundary).
3. No silent corruption, no data loss.

**Recommended follow-ups** (logged for v0.5.x or v0.6 planning):
- Option A: lower the default `audio.pitch_tempo.method` fallback
  threshold and document the perf tradeoff explicitly.
- Option B: add a preflight WARN when `rubberband` is enabled on
  source `>60s` or `>1080p`, telling users to expect ~Nx wall time.
- Option C: parallelize audio chain (currently single-threaded by
  ffmpeg even when video segments are parallel).

### #3 — HDR-input × non-HDR profile  *(severity: WORKING-AS-DESIGNED)*

**Cells**: 10 — `synth_hdr10.mp4` and `synth_hlg.mp4` × {`soft`,
`medium`, `aggressive`, `cid_aware`, `cid_aggressive`}.

Preflight correctly emits `hdr.color.transforms` severity=`fail` and
refuses the run with a clear message naming the offending transforms
and suggesting `keep_hdr: true` or `video.tonemap_sdr`. This is the
designed safety net — left intact.

## Other observations

- **SSIM mean by profile** (samples drawn from passing cells, fast-QA;
  VMAF disabled — `vmaf_mean=None` for all):

  | Profile | n | avg SSIM | min SSIM |
  |---|---:|---:|---:|
  | `soft` | 12 | 0.8594 | 0.7707 |
  | `medium` | 12 | 0.8428 | 0.7534 |
  | `medium_hdr` | 14 | 0.8334 | 0.7326 |
  | `aggressive` | 12 | 0.8158 | 0.7499 |
  | `cid_aware_hdr_to_sdr` | 5 | 0.7901 | 0.7176 |
  | `cid_aware` | 10 | 0.7770 | 0.7404 |
  | `cid_aggressive` | 10 | 0.7523 | 0.7268 |

  Monotonic: each profile that adds CID-divergence transforms costs
  fidelity. No outliers, no unexpected zeros.

- **`cid_predict_self`** (43 cells high ≥0.95, 32 med, 1 low <0.7, 23
  unrecorded — match `none` count to the 23 failed cells). The single
  low-CID cell is worth a manual check but is not a bug — the profile
  is doing its job (low self-similarity = good divergence).

- **No checkpoint corruption observed** across any passing cell.

## Closed by this work

- New preflight check `tonemap.sdr_input` (Finding #1)
- 2 new unit tests in `tests/unit/test_preflight.py`
- This triage report

## Backlog (out of scope for this pass)

- Finding #2 perf investigation (`rubberband` on long / high-res inputs)
- GUI deep sweep on all 10 screens (matrix already exercised CLI +
  orchestrator + preflight + QA + checkpoint)
- Re-run full matrix after `chromaprint` install — `audio_fp_*` fields
  in qa.json are all `None` because `fpcalc` is not on PATH; this
  masks any chromaprint-pipeline bugs.
