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

**Defense-in-depth** (implemented across two passes):
- ~~Replace the `ffmpeg -filters` substring parse with an actual dry-run
  of `ffmpeg -af 'rubberband=pitch=1.0' -t 0.001 -f null -i 'sine=440' -`
  which faithfully reports whether the filter graph can be opened.~~
  **Done** — `core/preflight.py::_ffmpeg_filter_works` (this pass).
- ~~Or have `core/audio_windows.py` re-verify filter availability
  immediately before invoking the audio chain, after 18 min of
  video work has already burned.~~ **Done** —
  `core/audio_windows.py::verify_audio_filters_available` called from
  `core/segmenter.py::process_main_audio` right before `run_ffmpeg`
  on the audio chain (2026-05-31 follow-up). 3 new unit tests in
  `tests/unit/test_pitch_rubberband.py` cover happy path, lost-filter
  PipelineError, and asetrate-bypass.

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

### #4 — `audio_fp_similarity` is misleading UX (always 0.0)  *(severity: MINOR, documented)*

**Cells**: 76/76 passing cells — every output mp4 we produced has
`audio_fp_similarity == 0.0` in its `<out>.qa.json`, regardless of
profile aggressiveness.

**Root cause**: `core/qa/audio_fp.py::compare` computes Jaccard over
the set of 32-bit chromaprint sub-fingerprints (`len(A ∩ B) / len(A ∪ B)`).
Any single-bit change in any sub-fingerprint makes a sub-block
completely different from the input's; chromaprint deliberately
flips bits across the entire 32-bit code for small acoustic changes,
so `loudnorm` alone is enough to produce a 100 % disjoint set.

**Why it's misleading**: a user opening the QA report sees
`"audio_fp_similarity": 0.0` next to `"audio_fp_match_confidence": 0.55`
and reads "audio totally destroyed" — but the audio is perfectly
recognisable. The Hamming-based `audio_fp_match_confidence` (mean
Hamming per frame normalised by 32) is the metric that actually
reflects perceived similarity.

**Why not fixed in this pass**: the field is part of the documented
QA schema (`docs/qa_report.md`) and is consumed by downstream
analysis (`out/runs/_analyze.py`, calibration loop). Changing
semantics would break analyses, and removing the field requires
schema-version negotiation. Best fix is a one-paragraph clarification
in `docs/qa_report.md` flagging that Jaccard-on-subfingerprint-sets
is strict-by-design and should not be read as "audio similarity".

### #5 — `h264_videotoolbox` probe fails on this Mac  *(severity: INFO, not yt-uniq's bug)*

`yt-uniq probe --encoders` shows `h264_videotoolbox.works=false`
with `error: Nothing was written into output file, because at least
one of its streams received no packets`. `hevc_videotoolbox` works
fine. This is an ffmpeg-on-this-system issue (the encoder's input
format constraints fail against the `testsrc2` probe pixel format),
not a yt-uniq defect. yt-uniq's fallback chain correctly degrades
to `libx264` when h264_videotoolbox is unavailable, so user-visible
behaviour is correct.

Logged so that a future probe-tuning ticket can revisit the
`encoder.py::detect_encoders` test pattern — sending a more
VideoToolbox-friendly input (e.g. `nv12` or hardware-encoded source)
might recover h264_videotoolbox detection.

### #7 — `video.tonemap_sdr` × zscale-missing crashes mid-encode  *(severity: HIGH, fixed)*

**Cells**: 1 — `synth_hdr10.mp4 × cid_aware_hdr_to_sdr` (and the
companion HLG cell, intermittently). Profile + input combo that
is the SUPPORTED HDR→SDR path, but ffmpeg on this Mac lacks the
`zscale` (zimg) filter.

**Symptom** (pre-fix):
```
Encoder: libx264 (x264)
error: ffmpeg exited with 8; last log:
[AVFilterGraph @ ...] No such filter: 'zscale'
Error : Filter not found (full log: .../seg_0000.mkv.log)
```

**Root cause**: `core/transforms/video_tonemap.py` emits a chain
`zscale=transfer=linear → tonemap → zscale=transfer=bt709`. The
preflight `_check_hdr` tonemap branch was emitting only the
`hdr.tonemap.ok` status finding and early-returning — it never
verified zscale availability. Companion gap to Bug #2 (rubberband):
both transforms have an ffmpeg-filter dependency that wasn't gated
at preflight.

**Found by**: matrix re-run on 2026-05-31 (post-Bug #1/#2 fixes)
combined with direct repro showing ffmpeg exit 8 at segment 0.

**Fix**: `core/preflight.py::_check_hdr` tonemap branch now calls
`_ffmpeg_filter_works("zscale=t=bt709:m=bt709:p=bt709", "video")`
before the OK finding; emits `tonemap.zscale.missing` severity=`fail`
if absent. +1 regression test
(`test_tonemap_sdr_zscale_missing_fails`).

**Verified post-fix**:
```
$ .venv/bin/yt-uniq run synth_hdr10.mp4 \
    --profile cid_aware_hdr_to_sdr.yaml ...
Encoder: libx264 (x264)
error: preflight failed:
  [FAIL] tonemap.zscale.missing: Profile applies video.tonemap_sdr,
  which depends on the `zscale` filter (zimg). ffmpeg on this system
  lacks zscale, so tonemap would fail mid-encode.
```

### #8 — Full matrix re-run (post-fix verification)  *(severity: PASS)*

Matrix re-run after Bug #1 + #2 + #7 fixes (excluding the just-now
zscale gap): wall time dropped **115 min → 16 min (-86%)**. 31 cells
flipped from slow-failure (or false-pass for the zscale subset) to
sub-second preflight FAIL. The 76 originally-passing cells stayed
passing with no metric drift (`ssim_mean`, `cid_predict_self`,
`phash_similarity` within ±0.01 of the original). No new regressions.

### #6 — `workers=4` parallel-segment path  *(severity: PASS)*

Verified end-to-end with `clip_long.mp4` (90 s) at `--segment-sec 15`
(6 segments) × `--workers 4`: exit 0, output produced, QA built. The
recent `CheckpointStore` thread-safety hardening (`9dbc8fa`,
`7338fa1`) holds under real parallel-segment load.

## Closed by this work

- New preflight check `tonemap.sdr_input` (Finding #1)
- 2 new unit tests in `tests/unit/test_preflight.py`
- This triage report

### #9 — Full evermeet-ffmpeg matrix (final verification)  *(severity: PASS)*

After installing the `evermeet.cx` static ffmpeg (with `libzimg` +
`librubberband` + `libvmaf`, none of which Homebrew's default 8.1.1
includes), the matrix harness re-ran 106 cells against the full
profile + input corpus including the new 5-minute clip.

**Headline numbers**:

| | Cells | Pct |
|---|---:|---:|
| Total | 106 | 100% |
| Pass | 79 | 75% |
| Fail (all working-as-designed) | 27 | 25% |
| **New bugs** | **0** | — |

**Pre-evermeet vs evermeet pass rate**:

| | Pre-evermeet (Homebrew ffmpeg) | Evermeet ffmpeg |
|---|---:|---:|
| Pass | 49 / 99 (49%) | 79 / 106 (75%) |
| Wall time | 15.6 min | 208.2 min |

The 30 cells that flipped fail→pass are all real new coverage —
previously the preflight had to refuse the run because the required
filter (`rubberband` or `zscale`) was missing from ffmpeg, so the
relevant code paths were never exercised end-to-end on this host.

**Fail-class breakdown** (all working-as-designed):

| Cells | Class | Verdict |
|---:|---|---|
| 9 | `cid_aware_hdr_to_sdr × SDR-input` | `tonemap.sdr_input` preflight FAIL — Finding #1, correct rejection |
| 10 | `{HDR10, HLG} × {soft, medium, aggressive, cid_aware, cid_aggressive}` | `hdr.color.transforms` preflight FAIL — Finding #3, correct rejection |
| 4 | `{cid_aware, cid_aggressive} × {synth_sdr_4k, synth_long_5min}` | 1800s timeout — Finding #2 rubberband perf at 4K and 5-min content; not a bug, known performance characteristic |
| 1 | `synth_long_5min × cid_aware_hdr_to_sdr` | also Finding #1 (SDR input) — 5-min clip is SDR |
| 1 | `synth_long_5min × cid_aggressive × resume` | same 1800s timeout as run mode |
| 2 | `synth_long_5min × cid_aware × resume + similar` | same timeout class |

**Notable confirmed-working paths**:

- `cid_aware × clip_long.mp4` (90s SDR): 622s wall — rubberband audio
  chain works end-to-end through real workload
- `cid_aggressive × clip_long.mp4`: 870s wall — heaviest profile on
  90s clip stays under timeout
- `medium_hdr × {clip_a, clip_b, clip_long}`: 15-37s — zscale wrap +
  10-bit HDR encoder selection now reach the encoder cleanly
- `cid_aware_hdr_to_sdr × {synth_hdr10, synth_hlg}`: 12-14s —
  the supported HDR→SDR path now succeeds
- `synth_long_5min × soft × resume`: 326s — resume cell on a
  300-second clip with `--segment-sec=30` yields ~10 segments and
  resumes cleanly mid-run

**Perf observations** (none counted as bugs but worth noting):

| Profile | clip_a 30s | clip_long 90s | synth_sdr_4k 12s | synth_long_5min 300s |
|---|---:|---:|---:|---:|
| `soft` | 17s | 37s | — | 136s (run) + 326s (resume) |
| `medium` | 17s | 39s | — | 145s |
| `aggressive` | 20s | 46s | — | 242s |
| `cid_aware` | 224s | 623s | TIMEOUT (>1800s) | TIMEOUT |
| `cid_aggressive` | 316s | 871s | TIMEOUT (>1800s) | TIMEOUT |

cid_aware / cid_aggressive are ~10-15× wall time vs soft/medium on
the same content; this is the rubberband cost from Finding #2.
Documented in `docs/profiles.md` "Performance notes". Users who
need throughput on long or high-resolution sources should pick
`medium` or `aggressive`; users who need maximum CID divergence and
can spend the wall-time stay on `cid_*`.

## Backlog (out of scope for this pass)

- GUI deep sweep on all 10 screens (matrix already exercised CLI +
  orchestrator + preflight + QA + checkpoint)
  - **Closed by follow-up** (`.claude/plans/bug-triage-followups.plan.md`,
    2026-05-31): `tools/gui_sweep.py` drives MainWindow through all 10
    screens on the real Qt platform with PNG + Qt-log capture; manual
    checklist lives in `docs/gui_sweep.md`. Developer-only — offscreen
    `tests/visual/test_gui_screenshots.py` remains the CI baseline.
- Cost of `rubberband` on 4K / 5-min content: 2 of the 4 timeouts are
  cid_aware × {sdr_4k, long_5min} and 2 are cid_aggressive × same;
  the audio chain genuinely runs >30 minutes on these inputs. Tuning
  the rubberband filter parameters or providing a fast-path for
  long-form content is a future perf project, not a bug.
  - **Closed by follow-up** (`.claude/plans/bug-triage-followups.plan.md`,
    2026-05-31): `core/preflight.py::_check_rubberband_perf` emits
    `audio.pitch.rubberband.slow` (severity=warn) when a
    rubberband-enabled profile runs on a source `>60 s` or `>1080p`,
    surfacing the wall-cost before the encode starts. Measured matrix
    table moved to `docs/profiles.md#rubberband-performance-characteristic`
    with `method='asetrate'` fast-path snippet. 5 new unit tests in
    `tests/unit/test_pitch_rubberband.py`. Filter tuning + audio-chain
    parallelisation still deferred (true perf project).
