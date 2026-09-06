# Benchmark Methodology and Baseline

## Bounded-QA v6 rerun — in progress

The fresh full-timeline run uses `manifest.long-v6.yaml`, processing/QA code
introduced in `7d3a95f`, and a new `results/long-v6-bounded-qa/` directory.
Implementation SHA-256 (source Python plus benchmark runner):
`77db749ccb021e22d18a4475a3ed5c2f19fcfe882e899aad57bc7cec567028d5`.
Later tooling/test/document commits do not change that implementation digest;
the encoder's eventual metadata records its actual launch HEAD.
Do not substitute the historical
baseline below for its pending results. Long-form pHash now retains compact hashes
instead of decoded image lists; the image-cache estimate is capped at 64 MiB (not
a total-process RSS limit). Reference generation can use a single-copy virtual
FFV1 concat when the old two-copy estimate cannot fit, without reducing coverage.
Measured file growth and a free-space reserve remain enforced. The fallback is
restricted to unchanged-duration plans; retimed references keep the existing guard.

Real FFmpeg regression checks compare physical/virtual reference pixels, decoded
timestamps, SSIM and available VMAF, including directories containing quotes/spaces.
The eventual three-hour report, not these short tests, must establish long-form
RSS, disk use, quality and A/V correctness. Concurrent development tests on this
Mac mean wall times are observational, not isolated performance comparisons.

Separate extraction stress check completed: two selections of **10,820 frames**
from a four-second 400×300 FFV1 derivative of the same licensed archival source
returned identical hashes, with **125,796 KiB** (~122.85 MiB) sampled process-tree
RSS and **158.719 s** wall time. Evidence: `.qualification/qa-memory-10820.log`
and `.qualification/qa-memory-10820.resources.json`. This exercises the legacy
three-hour sample count but is **not** the entire QA pipeline, independent natural
frames, or a measured three-hour speed/RSS improvement. The real full-run result
remains pending. All 16 shipped YAML profiles also validated without changes.

Expanded bitrate experiment (fresh result directory required):

```sh
.venv/bin/python -m tools.rate_control_experiment \
  validation-corpus/manifest.rate-control-expanded.yaml \
  --results validation-corpus/results/rate-control-expanded-v6 \
  --start-sec 15 --seconds 12 --repeats 3 --vbv-multiplier 2 --vbv-multiplier 4
.venv/bin/python -m tools.rate_control_experiment \
  --results validation-corpus/results/rate-control-expanded-v6 --assess-existing
```

Five files represent four upstream titles (PQ/HLG share Meridian). Each window
uses one identical transformed FFV1 reference and seed 42 for source cap, 2×/4×
bounded caps and CRF-only. This is engineering evidence, not threshold calibration
or permission to change defaults; repeated encodes are not independent content.

## Completed historical three-hour baseline — 2026-09-06

The previously running job has finished, including full source/output decode.
It is **not a fresh v1.6.0 / encode-policy v6 benchmark**: its retained metadata
reports package 1.5.0, git `0cd70bb` and process-start implementation SHA-256
`0187e9f8fb3fed3edcbc9ddb4bc74bb1bdac9562b7c60f501faf33ff842b7348`.
Source SHA-256: `2642337aeabc7ef77e21efba895b6b3c17088e0d033e1e0b3d7f82445c087442`.
This is a public-domain archival 400×300, 29.97 FPS engineering fixture, not
modern dialogue, natural-camera HDR or three-hour 4K qualification.

| Measurement | Source | Historical soft/libx264 output |
|---|---:|---:|
| Fully decoded video frames | 324,395 | 324,395 |
| Missing / non-increasing PTS | 0 / 0 | 0 / 0 |
| Decoded audio samples / rate | 477,325,312 / 44,100 Hz | 519,551,552 / 48,000 Hz |
| Video end, seconds | 10,823.990658 | 10,823.983000 |
| Audio end, seconds | 10,823.703220 | 10,823.990667 |
| Audio minus video end | −287.438 ms | +7.667 ms |
| Integrated loudness / true peak | −23.44 LUFS / +2.57 dBTP | −14.63 LUFS / −1.79 dBTP |
| File bytes | 769,585,376 | 1,741,605,990 (2.263×) |

Output samples exactly equal `round(10823.990657 × 48000)`: zero sample delta
from the declared padded output timeline. Direct source/output sample subtraction
would be misleading because the source is 44.1 kHz and its audio ends early.
Matching frame counts/monotonic timestamps do not prove every decoded picture's
identity; matching endpoints do not disprove the independently measured internal
audio drift of 0 / −50 / −100 ms at start/middle/end.

Processing wall time: **6,947.27 s** (115 min 47 s), sampled process-tree peak
RSS **300,684 KiB**. QA separately took **4,109.30 s** with **6,171,004 KiB** peak
RSS (~5.89 GiB). Sampled peak logical working/output bytes: **3,475,706,195**;
this is a lower-bound sampled disk footprint, not cumulative physical I/O.
These phase measurements do not include every later diagnostic pass and are not
an isolated-host performance comparison.

Raw VMAF **14.258758**, SSIM **0.750925**, PSNR **22.207173 dB**. Legacy QA is
**RED**, and the benchmark cell remains `ok=false`, `metrics.complete=false`:
registered VMAF/SSIM could not run because the reference needed 38,927,438,926
bytes versus 23,294,954,700 allowed by the disk guard. No guard was bypassed.
Raw source/derivative scores include intentional transform differences; unavailable
registered metrics must not be presented as validated encoding quality.

Retained JSON/CSV/HTML and per-phase evidence:
`validation-corpus/results/extended-long-180m/summary.{json,csv,html}` and
`historical-180m-av__current/`. Completion of measurement is **not** production
acceptance. A full v6 rerender, adequate registered-reference storage/bounded
reference design and human quality/listening review remain open.

## Controlled source-cap versus CRF — 2026-09-06

Completed 18 video encodes: three licensed six-second excerpts × two policies ×
three repeats, alternating arm order, fixed seed 42, libx264 CRF 18 and identical
existing segment filter/GOP commands. Only `-maxrate`/`-bufsize` were removed in
the experimental arm. Each pair uses the exact same transformed lossless FFV1 SDR
reference; all 18 decoded frame-count/PTS contracts passed independent assessment.

| Case / policy | VMAF median | SSIM median | PSNR dB | Bytes median | Encode s median |
|---|---:|---:|---:|---:|---:|
| SDR dialogue / source cap | 94.753 | 0.993140 | 47.178 | 2,213,530 | 6.06 |
| SDR dialogue / CRF only | 94.753 | 0.993140 | 47.178 | 2,213,460 | 9.24 |
| PQ→SDR dark/skin / source cap | 74.588 | 0.958323 | 41.572 | 274,591 | 15.90 |
| PQ→SDR dark/skin / CRF only | 89.282 | 0.962975 | 43.681 | 3,528,614 | 19.92 |
| HLG→SDR motion / source cap | 76.284 | 0.957016 | 41.939 | 341,158 | 15.62 |
| HLG→SDR motion / CRF only | 90.267 | 0.961773 | 43.361 | 3,832,537 | 17.66 |

Retained: `validation-corpus/results/rate-control-v160/` (`results.json/csv/html`,
`assessment.json`, `review.html`, source hashes, exact commands, profiles, per-arm
process-tree RSS/time and reference/output files). Three distinct files represent
only two upstream titles: PQ/HLG are derived natural pictures, not independent
camera-native HDR masters. Measurements shared host load with long-form QA/tests;
timing/RSS differences are **not isolated performance regressions**. The reference
and diagnostic tooling were edited during the session; final assessment separately
checks all retained output frames without re-encoding or relabelling provenance.

Conclusion: source-derived bitrate alone is not a safe quality budget for these
tonemapped/noisy outputs. Removing the cap improved VMAF by about 14–15 points but
increased bytes about 11–13×; SDR control was unchanged. Production defaults are
unchanged pending a bounded, explicit rate-control policy and broader evidence.

### Calibration status

Observed CRF-only encoding-loss bands: VMAF 89.282–94.753; SSIM 0.961773–0.993140;
PSNR 43.361–47.178 dB. These are **not production thresholds** and do not judge
intentional transform quality, lip sync or HDR mastering. No human accept/reject
labels or independent held-out titles exist for this set. `assessment.json`
therefore records `proposed_production_thresholds: null` / `NOT VERIFIED`; repeats
are not counted as independent content. Existing loudness measurements remain
separate full-track measurements, not arbitrary corpus-derived LUFS defaults.

### Internal audio evidence

Natural 4K/5.1 corrected excerpt: five active channels measure -10 ms envelope lag
at 10-ms resolution; silent LFE is inconclusive. A speaker-labelled FLAC/MKV fixture
passes the full segmented pipeline with six channels in order and all 150 decoded
flash positions unchanged. The test passed native FFmpeg 9 and Linux FFmpeg 5/6.

Legacy three-hour stereo baseline: source/output envelope lags are 0 / -50 / -100 ms
at 0 / 5400 / 10800 seconds (correlations about 0.95 / 0.99 / 0.98). Matching final
duration did not establish internal sync. The current transform reproduced a
quantization defect in three failing clock tests: at 44100 Hz and pitch 1.0004,
FFmpeg rounds the new clock to 44118 Hz, but nominal compensation 0.999600 creates
8 ppm of speed error (~86.4 ms/3 h). Compensation now uses the actual integer clock,
12-digit precision where needed, and avoids nonidentity WSOLA work at unity tempo.
The legacy long output is retained unchanged; it is not a full v6 re-encode or
proof of human lip-sync acceptance.

The new 30-minute synthetic late-event regression passed on native FFmpeg 9 and
Linux FFmpeg 5/6: legacy compensation shifts the event by more than 20 ms; fixed
clock/unity handling keeps it within 5 ms. This validates the regression, not a
replacement full three-hour encode under policy v6.

## QA contract v1.6.0 smoke (RFC #21)

The retained 4K/5.1 `audio-origin-corrected-retry/output-video-only-concat.mp4`
was compared to itself with `--no-vmaf --no-audio-fp --no-cid-predict --samples 8
--min-ssim 0.999 --loudness`. Real full-decode correctness passed; SSIM was 1.0;
full-stream loudness was -15.70 LUFS / -1.84 dBTP. JSON/HTML artifacts:
`.qualification/qa-rfc21-natural.{json,html}`. This is an identity/measurement smoke,
not source-versus-derivative quality acceptance or a benchmark of encoding speed.
The earlier 20-second excerpt LUFS is deliberately a different measurement scope.

## Extended Intel Mac experiment — 2026-09-06 (qualification in progress)

Sources/rights/hashes: `validation-corpus/open-sources.yaml`; derivations and exact
commands: `validation-corpus/DERIVATIONS.md`. New 4K/5.1 and continuous 176/180-minute
sources are not synthesized loops. Historical low-resolution films do not qualify
modern 4K movies. HDR cases remain explicitly derived natural-picture PQ/HLG, not
camera-native HDR10 evidence.

Local verification: broad `make check` completed with 1747 passed / 55 expected
skips before the final cross-version timestamp corrections. The corrected tree
then passed 1479 unit/contracts, native FFmpeg 9's 19 media contracts, FFmpeg 5/6
container/rate matrices (16 each), and real leading-chapter-gap and stereo/5.1
encoded-peak tests. Ruff, strict mypy (163 source files), and wheel build passed.
These separate runs are not represented as a final full-suite run on one revision.

Final code `8cfb11e` subsequently passed the complete native `make check`:
**1790 passed, 55 skipped, one Starlette deprecation warning, 1257.03 seconds**.
Ruff and strict mypy (163 source files), wheel and Intel GUI builds passed.
Skips include the separately opted-in hardware matrix, heavy GUI E2E and the
missing-PyQt branch in this GUI-equipped environment; they are not qualification
of those paths. Real audio regressions also passed on Linux FFmpeg 5/6. CI
`34030418205` passed all six OS/Python jobs; release/Docker proofs are recorded in
`PRODUCTION_CHECKLIST.md`. The separate 180-minute job started before these fixes;
its completed historical results are now recorded at the top of this document.

- 4K soft cell: 31.436 s, encode 82.72 s, sampled process-tree RSS 2,456,372 KiB,
  output 41,973,462 bytes, raw VMAF 3.728567 versus registered 93.811511,
  registered SSIM 0.986410. High registered quality does not remove the original
  scene/timeline difference. Medium cell encode: 247.75 s, RSS 2,383,384 KiB,
  sampled work/output logical peak 83,683,340 bytes. Runs shared host load and
  are **not an isolated speed comparison**.
- Natural 5.1 delivery defect reproduced: loudnorm reports -1.50 dBTP; resampled
  float PCM measures -1.05; full AAC output +0.27. A fast input-seek review excerpt
  initially measured +1.40; switching review extraction to decode-before-trim
  removed that seek artifact but retained the genuine +0.27 full-stream overshoot.
- Bounded source re-render with linked headroom delivered -3.38 dBTP. The 20-second
  A/B region contains 960,000 samples/channel and no full-scale/nonfinite values;
  source/current/corrected LUFS are -23.17/-13.68/-16.64. This is a peak-safety fix,
  not a claim to still meet -14 LUFS or to pass human listening.
- Stereo review: source/current/medium excerpt LUFS -9.49/-13.24/-13.26 and
  peaks +0.77/-1.50/-1.50 dBTP, confirmed by decode-before-trim re-extraction.
  Source has 352/417 full-scale samples, both processed excerpts have zero. Retain full-file
  and excerpt scopes separately; cuts can alter measured boundary transients.
- Numeric counters are streamed, not retained per frame in RAM. Disk values are
  1-second sampled logical-file lower bounds for work/output directories, excluding
  OS-temp QA references. QA time/RSS are now measured separately in the runner.
- Both 4K variants and the corrected-audio mux decode to 753 video frames and
  1,508,928 audio samples/channel; video ends at 31.375 s, audio at 31.436 s
  (+61 ms). Source already has a +62.33 ms endpoint delta and delayed audio start;
  the increased decoded sample count alone does **not** demonstrate correct padding
  placement. Subsequent 10-ms RMS-envelope correlation exposed a 1.32-second advance
  in both baseline and peak-only-corrected audio, matching the source's 1.313-second
  leading gap. These files must not be used as synchronization-qualified outputs.
  A new origin correction materializes silence before tempo/PTS operations; real
  130-second delayed-pulse tests at 1x/2x pass on FFmpeg 5/6/9. Natural rerender and
  independent editorial lip-sync assessment remain separate checks.
- Natural origin-corrected rerender: existing baseline video stream copied without
  another video encode; audio rendered again from the original source. Staged full
  decode/contract/peak validation passed. Delivered full-stream peak is -1.84 dBTP
  after two source renders and -1.87 dB linked gain. On the same 20-second region
  starting at 2 s, decoded mono 8 kHz / 10-ms RMS-envelope cross-correlation within
  ±2 s measures baseline lag -1.32 s (0.7195 correlation), corrected lag -0.01 s
  (0.7046). This measures relative source alignment, not editorial lip-sync or a
  human listening verdict. Artifacts: `results/audio-origin-corrected-retry` and
  `results/listening-surround-origin-corrected` under `validation-corpus/`.
- Final isolated remux uses an extracted video-only MKV as concat input, matching
  production's video-only segment contract: `output-video-only-concat.mp4`.
  Passing the original audio-bearing MP4 directly to concat introduced a 21.313-ms
  priming-related video offset in the first diagnostic remux; that is not the final
  comparison file. Final video/audio start at zero, with 753 frames, 1,508,928
  audio samples/channel, ends 31.375/31.436 s and no PTS defects. Compressed video
  payload SHA-256 is identical before/after:
  `b70161be0af08c48c34e6d7ebfc4b7296aa1fd464038914775e58852b35955ef`.
  Corrected 20-second 5.1 excerpt measures -15.37 LUFS / -1.84 dBTP, zero full-scale
  or nonfinite values in every channel. Peak safety still does not imply -14 LUFS.
- 176-minute video-only encode completed: 18 segments, 1,451.65 s pipeline wall,
  245,832 KiB sampled RSS, 2,042,135,805 sampled workspace/output bytes and
  1,022,159,764-byte output. Full decode counted 317,288 source and output frames,
  zero missing/non-increasing PTS; video endpoint delta is -2 ms. The runner exits
  1 (incomplete), correctly reflecting unavailable required registered metrics.
- First HDR natural-picture pair: current registered VMAF/SSIM 78.623/0.958818;
  tonemap-only proposed 89.128/0.995036. Each score uses its own transformed
  reference, so the difference does not prove that the tone curve itself is better.
  Raw HDR-to-SDR VMAF is not a perceptual HDR mastering verdict.
- All four PQ/HLG cells completed; every output retained 1799 decoded frames,
  with zero missing/non-increasing PTS. HLG current/proposed registered VMAF is
  79.237/91.031 and SSIM 0.958846/0.995706. Human HDR approval remains unverified.
  QA peak RSS reached 9,954,512 KiB, versus approximately 1 GiB for those encodes;
  deployment RAM budgets must account for QA separately.
- Full 176-minute registered reference was refused by its disk guard:
  38,074,558,128 estimated bytes versus 29,061,047,910 allowed bytes. No disk
  safety override was used. Raw VMAF/SSIM are 39.207605/0.902761; registered
  quality is **NOT VERIFIED** on this film.

Reports/audio live under ignored `validation-corpus/results/extended-*` and
`listening-*`. Completed historical long-form decode accounting is recorded above;
revision-specific CI evidence is in the checklist. No incomplete experiment is
counted as passed. Human visual/HDR/listening verdicts
are **NOT VERIFIED**.

Baseline: 2026-09-02, commit `14df893`; production acceptance дополнен
2026-09-05. Результаты относятся только к указанному локальному environment и не
экстраполируются автоматически на фильмы/HDR/GPU.

## Production acceptance delta — 2026-09-05 (post-v1.5.0 integration)

На Intel macOS с Homebrew FFmpeg 9.0.1 дополнительно подтверждено:

| Проверка | Результат | Инженерное решение |
|---|---:|---|
| HLG → `medium_hdr` → libx265 | 100/100 frames, video/audio 4.000 s, `yuv420p10le`, HLG/BT.2020/tv | HLG preserve path разрешён; final validator проверяет полный color contract |
| HLG → HEVC VideoToolbox | 100/100 frames, 4.000 s, `yuv420p10le`, HLG/BT.2020/tv | Hardware HLG подтверждён только для этого Mac |
| 2 × concurrent H.264 VideoToolbox 1080p | 180/180 frames каждый; 7.72 s/job, 8.09 s aggregate wall | `max_parallel=2` подтверждён для этого Mac |
| MP4/MKV/MOV, 3 s tagged SDR | 7/7 integration tests; all A/V streams decode; chapters/subtitles retained by policy | Container smoke закрыт для synthetic core matrix |
| MKV attachment / MOV tmcd / MP4 cover art | Attachment bytes exact; `01:00:00:00` retained; JPEG bytes exact | Supported auxiliary topology is preserved and final-contract validated |
| ASS subtitle → MKV/MOV | ASS copied to MKV; converted to `mov_text` in MOV; language/title retained | Text subtitle policy confirmed |
| MIT-licensed real PGS fixture | MKV extraction is byte-identical; MP4/MOV fail preflight before encode | Image-subtitle container contract confirmed with `hdmv_pgs_subtitle` bytes |
| APFS queue, 4 processes × 80 jobs | 80 unique leases, 0 duplicates/losses | Single-host atomic lease contract подтверждён; NFS не проверен |
| Segmented VFR, libx264/VideoToolbox, 6 s | 220/220 frames; 30/20/60 FPS cadence retained; monotonic PTS; A/V end delta ≤50 ms | Software plus Intel H.264/HEVC VideoToolbox preserve contract разрешён; other hardware remains unverified |
| Temporal jitter, maximum 0.2/0.2 | bounded 289-character filter; 24/30/60 FPS retained 58/72, 71/90, 145/180 frames; VFR cadence classes retained | Pattern is based on a 24 Hz PTS grid instead of source frame number; hardware paths remain unverified |
| Auto encoder policy, H.264 | `quality` → libx264; `speed` → H.264 VideoToolbox | Default follows production quality priority; hardware requires explicit policy/override |
| `soft`, 30.183 s, policy smoke | libx264: 17.86 s, 752 frames; VideoToolbox: 9.36 s, 752 frames; both A/V start 0 | Selection policy changes throughput without losing decoded frames on this Mac |
| Benchmark RSS sampler, 10.01 s fixture | 497,832 KiB peak, `psutil_process_tree_sum_100ms` | New result includes simultaneous Python/FFmpeg RSS; single run, not a regression baseline |
| Correctness-first QA, video-only candidate | real MP4 missing source audio → `INVALID`; full candidate decode passed | Sampled visual similarity cannot hide missing topology |
| Mandatory decode gate, 3 s H.264/AAC MP4 | valid output passed; copy truncated by 8 KiB failed as `output.decode` on this Mac | Every `run_full` frontend now rejects corrupt tails before completion/publication |
| Chromaprint dependency repair | `fpcalc 1.6.1`; 12 s identity audio similarity 1.0/Hamming 0 | Optional audio diagnostics operational on this Mac |
| Long-form fingerprint smoke, 620 s AAC | five ordered windows, 600 s total coverage, identity 1.0; 3.73 s wall | Start/middle/tail coverage replaces first-600-only report path |
| SSCD extraction, 30 s 640×360, 32 frames | 32 processes / 6.942 s → 1 process / 0.931 s; 32/32 PNG SHA-256 identical; 7.45× wall speedup | Batch midpoint seeks without changing sampled bytes; single Mac run, not a general throughput claim |
| Full local quality gate after Phase 5 | 1466 passed, 2 expected skipped; Ruff + strict mypy pass; 22:13 | QA changes did not regress the full Mac suite or stable contracts |
| Full local gate after mandatory decode validation | 1509 passed, 2 expected skipped; Ruff + strict mypy pass; 21:18 | Shared final decode gate is compatible with the complete Mac FFmpeg/GUI/test matrix |

Natural-content viewing/listening, NFS cross-host, NVENC/QSV/AMF и YouTube
ingestion/transcode остаются `NOT VERIFIED`.

## Production hardening delta — 2026-09-04

На том же Intel Mac / FFmpeg 9.0.1:

| Проверка | Результат | Инженерное решение |
|---|---:|---|
| Registered seam diagnostic | Clean FFV1 source/output passes; black boundary defect fails; 8 focused tests | Tool compares source/output with reset PTS and ±frame registration instead of output/self motion |
| Audio layout/rate matrix | 24 real-FFmpeg cases: 7 effects × mono/stereo/5.1 plus 44.1/48/96 kHz | Tested effects preserve channel count; Haas stays stereo-only fail-closed; final rate is 48 kHz |
| Windowed audio boundary events | Six pulse markers before/on/after the 60 s and 120 s crossfades retained exactly once within 30 ms; 180 s duration stayed within one AAC-frame budget | Crossfade assembly does not drop, repeat or accumulate timing error on the synthetic divergent-EQ fixture |
| libx264 H.264 upload structure | 24 FPS output: High Profile, CABAC, max B-run 2, four IDR GOP starts across 48 frames with max interval 12 | Software H.264 no longer relies on library GOP/B-frame defaults; other locally available codecs are covered by the separate matrix below |
| Local HEVC/AV1 bitstream matrix | 5/5: libx265, SVT-AV1, libaom-AV1, H.264 and HEVC VideoToolbox; 144/144 frames, tagged BT.709, expected profiles/pixel formats and bounded GOPs | VideoToolbox HEVC profile is pinned to Main/Main10 by output depth; H.264 VideoToolbox produced CABAC/IDR and a one-frame B-run on this Intel Mac; GitHub Apple Silicon produced a three-frame run and now receives explicit half-second IDR requests because its backend can accept `-g` yet exceed it under load |
| Strict self-hosted qualification harness | Final run `33966394736` on `6aa0720`, mandatory selection `h264_videotoolbox,hevc_videotoolbox`: 22 passed, 36 unrequested cases skipped in 162.43 s; JUnit plus 39 hashed/probed media artifacts collected | Bitstream, 1080p/4K SDR, exact VFR PTS/frame count, HEVC HLG, static-HDR10 fail-closed policy, cancellation, fallback and two-session concurrency passed on this Intel Mac; ephemeral runner removed itself after the job; NVENC/QSV/AMF remain `NOT VERIFIED` |
| Debian 12 production container bitstream matrix | FFmpeg 5.1.9: libx265/SVT-AV1/libaom-AV1 3 passed; two unavailable VideoToolbox cases skipped | Current wheel and software encoder policy work on the shipped Linux runtime, including libaom constant-quality mode |
| HEVC/AV1 two-second GOP synthetic delta | 6 s, 640x360, 24 FPS: keyframes 0/48/96; file-size change vs defaults: x265 +1.48%, SVT +0.96%, libaom +1.06%, HEVC VideoToolbox -3.57% | Small synthetic result supports predictable random access; natural-corpus size/quality impact remains required |
| Multi-audio codec/container matrix | AAC main + Opus secondary across MP4/MOV/MKV | MP4/MOV transcode unsupported passthrough to AAC; MKV preserves Opus and stream metadata |
| SRT/ASS/PGS container matrix | Text formats passed MP4/MOV/MKV; real PGS is byte-identical through MKV and rejected before MP4/MOV encode | Subtitle policy is executable with text and image-based fixtures |
| Exact media deltas | 3 s MP4/MOV/MKV: 72/72 decoded video frames; normalized 48 kHz audio sample delta ≤1024 and packet-end delta ≤50 ms | Found and fixed a real loudnorm PTS discontinuity that previously cut 3581 decoded source-relative samples during final mux limiting |
| Loudnorm mode observability | 44.1 kHz transformed fixture requested linear; FFmpeg reported dynamic; output remained -14.0 LUFS | Runtime mode and fallback reason are now retained instead of assuming measured input guarantees linear processing |
| Seed/resume reproducibility | Fresh invocation seed differs, persisted seed is restored; retained segment/audio mtimes+hashes and decoded A/V SHA-256 remain equal | Resume does not reroll stochastic transforms or reprocess completed media |
| HDR regression | HDR10 preserve, HLG preserve and HDR10→SDR: 4 passed | x265/zscale/tonemap paths remain qualified on this Mac |
| SDR range roundtrip | Full (`pc`) and limited (`tv`) survive segment encode/concat | Generic FFmpeg tags and x264 bitstream range flags agree with decoded output |
| libaom discovery | Generic probe 15.63 s/timeout → probe-only fast settings 1.5–1.9 s/pass | Working libaom is no longer hidden; production CQ arguments are independently exercised by the bitstream matrix |
| Web/plugin security | 57 plugin/web tests plus direct body-limit and SlowAPI regressions | Missing/malformed/negative/duplicate/conflicting/oversize lengths fail closed; sandbox/allowlist/path/rate boundaries are exercised |
| Repository/artifact secrets | Gitleaks 8.30.1: 324 commits / 4.70 MB and local artifacts / 173.81 MB, zero findings | Repository and current build outputs pass the local secret gate |
| Workflow static analysis | actionlint 1.7.12: pass | Release shell snippets use safe artifact discovery/globbing |
| Full local quality gate | 1725 passed, 55 expected hardware/optional skips; Ruff + strict mypy (162 files) pass; 15:53 | HDR colour-domain, exact long-form audio-tail, resource-budget and fault-lab additions do not regress the complete Mac suite; skips are unrequested hardware qualification cells |
| Remote release-candidate matrix | commit `6aa0720`, run `33966344170`: 6/6 Linux/macOS/Windows Python 3.11/3.12 jobs passed; CodeQL run `33966344225` passed with zero open alerts | The Apple Silicon H.264 VideoToolbox IDR regression and all supported native CI OS contracts pass on the final tree; full vendor hardware qualification remains separate |
| Scheduled performance replacement | commit `6aa0720`, run `33966849505`: 20 s `cid_aware` stereo fixture completed in 58.77 s with 431,932 KiB peak RSS; baseline 58.31 s / 430,976 KiB, deltas +0.8% / +0.2% | Verdict `within threshold`; regression issue step skipped. The old mono fixture failure was a correct Haas stereo preflight rejection, not a timing regression |
| Manual release assembly | commit `3ec20ce`, run `33964477926`: Linux/macOS/Windows GUI bundles and AppImage passed embedded-version/runtime checks; downloaded candidate passed ZIP integrity, all six SHA-256 entries and all seven keyless cosign-bundle verifications | The workflow can assemble a complete v1.5.0 candidate without publishing a tag; CycloneDX 1.5 contains 59 components and the AppImage independently reports `1.5.0` in clean Ubuntu amd64 |
| CI-equivalent coverage | 1497 passed, 12 expected skips, 198 deselected; 81.23% branch-aware core coverage on Ubuntu/Python 3.12 | Required 80% gate passes on integrated `main`; v1.5.0 wheel and sdist build successfully |

The no-upscale and raw/registered metric contracts from GitHub RFC #11 and #12 are
integrated in `main` under the documented repository-owner review-window override.
Both RFC issues are closed as completed after local and six-cell remote qualification.

RFC #12 integrated synthetic qualification on the current Intel Mac:

| Case | Result | Decision enabled |
|---|---|---|
| Mirror, crop, 1.02× speed, deterministic 10% frame drop | 4/4 real-FFmpeg cases passed; registered SSIM > 0.97 | Exact Plan/segment-seed replay follows spatial and temporal transforms |
| Mirrored H.264 output with local libvmaf | Registered VMAF > 95 | Registered scorer command, local PTS reset and FFV1 reference are operational |
| Audio fixed offset + bounded linear drift | Exact synthetic alignment recovered; low 25% overlap rejected | Offset/drift diagnostic cannot win on a short matching excerpt |
| SSCD monotonic alignment/cache | Identity/adversarial/no-reuse, corrupt-cache recovery and one-hour sparse-grid time bound passed | No output-frame reuse; cache corruption and long-form offset bounds fail safely |

These are deterministic synthetic regressions, not natural-content thresholds. Licensed
speech/music/HDR viewing and listening remains `NOT VERIFIED`.

## Checksum-pinned open-content qualification — 2026-09-05

Для воспроизводимого локального smoke скачаны и проверены по exact byte size/SHA-256:
Netflix Meridian (CC BY 4.0), Blender Foundation Tears of Steel (CC BY 3.0) и
public-domain Night of the Living Dead. Publisher-labelled Meridian P3/PQ MP4 фактически
не содержит читаемых HDR tags и является 8-bit H.264, поэтому PQ/HLG fixtures получены
явной документированной 10-bit conversion и не выдаются за native-camera HDR masters.

Короткая natural-scene matrix завершила все `6/6` cells с exit code 0:

| Case / profile | Registered quality | Other measured results | Decision |
|---|---:|---:|---|
| SDR 60 s / `soft` | VMAF 97.431; SSIM 0.98949 | PSNR 23.88 dB; -14.64 LUFS; -1.47 dBTP; size 2.65×; 55.32 s | Current quality-first baseline |
| SDR 60 s / `medium` | VMAF 96.925; SSIM 0.97607 | PSNR 22.78 dB; -14.62 LUFS; -1.44 dBTP; size 3.25×; 64.66 s | Not promoted: vs soft VMAF -0.506, SSIM -0.0134, size and wall time worse |
| Derived HDR10 preserve / `medium_hdr` | SSIM 0.84198; ordinary VMAF N/A by policy | 10-bit PQ/BT.2020 + ST2086/CLL exact; PSNR 25.58 dB; 89.43 s; 1,302,872 KiB encode RSS | Preserve contract passes; metric band is not yet a release threshold |
| Derived HLG preserve / `medium_hdr` | SSIM 0.93668; ordinary VMAF N/A by policy | 10-bit HLG/BT.2020 exact; PSNR 28.84 dB; 84.03 s; 1,251,080 KiB encode RSS | Preserve contract passes; native HDR corpus still needed |
| Derived HDR10 → SDR | VMAF 78.774; SSIM 0.95870 | BT.709 output; PSNR 19.41 dB; size 0.57× | Experimental; below proposed VMAF band, do not promote |
| Derived HLG → SDR | VMAF 79.221; SSIM 0.95876 | BT.709 output; PSNR 16.82 dB; size 0.49× | Experimental; below proposed VMAF band, do not promote |

The first natural HDR preserve run exposed a severe green/orange cast in bright blinds
and contours even though all metadata remained correct. Root cause was transfer-only
linearisation in subsampled YUV. The corrected graph uses `gbrpf32le` linear light and
an explicit BT.2020/10-bit return. Four-frame tone-mapped A/B contact sheets show neutral
highlights after the fix; a real independent FFV1-vs-HEVC integration regression now
requires registered SSIM > 0.95 on the synthetic contract. The natural HDR10 score moved
from 0.83488 to 0.84198 and HLG from 0.93456 to 0.93668; those small metric changes also
show why metadata or a single scalar score cannot replace viewing representative scenes.

Natural stereo diagnostics on the SDR 60 s clip found 1440/1440 video frames for source,
soft and medium. Decoded output audio differs from the source by 381 samples (soft) and
485 samples (medium), both within one AAC-frame allowance; duration is 59.999 s. Outputs
contain no NaN/Inf/denormal samples, peak near -1.46 dBFS, do not exceed the source's
maximum adjacent-sample jump, and produced no sustained out-of-phase interval in FFmpeg
`aphasemeter`. Human speech/music listening remains `NOT VERIFIED`.

Current-tree platform packaging/runtime smoke also passed: Actions hardware run
`33966394736` on `6aa0720` reports strict Intel VideoToolbox
`22 passed / 36 unrequested skips` in 162.43 s with 39 probed/hashed media artifacts;
fresh Docker `linux/amd64` and QEMU `linux/arm64` images both completed build, A/V CLI
encode, ffprobe codec check and `/healthz`. Compose runtime inspection confirmed
`NanoCpus=4e9`, `Memory=12884901888`, `PidsLimit=512` and a healthy service.

The 95-minute public-domain natural-film run completed 10 segments with `soft`,
libx264 and two workers. It retained `171345/171345` decoded frames and produced
824,065,641 bytes from a 596,645,320-byte source (`1.3812x`) in 1,806.7 s, with
475,352 KiB measured peak process-tree RSS. The initial main-audio graph exposed a
2,848-sample delivery-rate tail loss and a 146 ms output A/V end gap. After the exact
48 kHz pad/trim fix, a full replacement audio encode and complete remux decode retained
exactly 274,426,152 samples over 5,717.2115 s and reduced the end delta to 4.5 ms,
while keeping 171,345 frames. Objective audio analysis measured -14.0 LUFS-I,
-1.4 dBTP and zero NaN/Inf/denormal samples. The fixed complete orchestrator is covered
by the real-FFmpeg sample regression and seven-boundary resume matrix; the 95-minute
video was not needlessly re-encoded a second time because its bytes are unaffected by
the audio-tail graph. Registered full-film VMAF/SSIM replay remains `NOT VERIFIED`:
the lossless reference estimate exceeded the safe free-disk budget on this Mac.

## Какие решения принимает каждая метрика

| Metric | Engineering decision | Ограничение |
|---|---|---|
| VMAF | Достаточен ли perceptual quality при фиксированной геометрии/timeline | Без registration crop/PTS shift смешиваются с compression loss |
| SSIM | Найти structural degradation и сравнить encode ladder | Слабее коррелирует с perception, чувствителен к alignment |
| PSNR | Обнаружить грубую pixel error/регресс encoder | Не использовать как единственную quality цель |
| LUFS-I | Соответствует ли programme loudness профилю | Не гарантирует отсутствие clipping |
| True peak | Безопасен ли headroom после всей audio chain | Измерять после resample/encode |
| Duration + per-stream start/end | Есть ли desync, padding, trim | Container duration одного недостаточно |
| Decoded frames/audio samples | Потеряны/дублированы ли данные | VFR требует сравнения по PTS/content, не только count |
| File size/bitrate | Storage/upload budget при прошедшем quality gate | Меньший файл не является сам по себе улучшением |
| Wall time | Throughput/ETA/capacity | Разделять cold/warm cache и no-op resume |
| CPU time/utilization | Worker scheduling и oversubscription | Нужен process-tree measurement |
| GPU utilization/VRAM | HW worker count и OOM avoidance | Vendor-specific tooling, optional |
| Peak RSS | Memory limit/container sizing | Tool records aggregate live process-tree RSS every 100 ms when `psutil` is available; fallback is explicitly labelled |

## Обязательный corpus

Минимальная production matrix должна использовать короткие deterministic fixtures и
отдельный лицензированный natural-content corpus:

| Axis | Cases |
|---|---|
| Duration | 30 s, 5 min, 1 h, 2 h, 3 h+ |
| Resolution | 720p, 1080p, 2160p; low-res no-upscale case |
| Dynamic range | SDR BT.709, HDR10 PQ+ST2086+CLL, HLG |
| Cadence | 23.976/24/25/29.97/30/50/59.94/60 CFR, VFR, long GOP |
| Audio | 44.1/48/96 kHz; mono/stereo/5.1; multiple default/language tracks |
| Container | MP4, MKV, MOV |
| Subtitles/data | none, SRT/ASS, PGS, chapters, attachments |
| Content | dialogue, music, grain, animation, dark HDR, fast motion, static scenes |

Для каждого case сравниваются:

```text
source
  -> encode-only control (тот же codec/rate-control, transforms off)
  -> current/proposed profile
  -> optional YouTube round-trip sample when legally/operator available
```

Так encode loss отделяется от transform loss. Для geometry transforms нужны две
оценки: raw end-to-end и registered/reference-transformed. Нельзя скрывать raw score,
если registration улучшил aligned score.

## Baseline environment

- macOS 26.6.2 x86_64, 12 logical CPUs, 32 GiB RAM.
- Homebrew FFmpeg-full 9.0.1 with `zscale`, `rubberband`, `libvmaf`, x264/x265,
  SVT-AV1 and VideoToolbox; Chromaprint/fpcalc 1.6.1; Python 3.12 `.venv`,
  package 1.4.0 candidate.
- Available locally: libx264, libx265, libsvtav1, libvmaf,
  H264/HEVC VideoToolbox.
- Installed optional stacks: GUI/QtCharts, scene/OpenCV, Torch 2.2.2 + torchvision
  0.17.2 + NumPy 1.26.4, web, crypto, observability and docs.
- Input: existing `tests/fixtures/.gen/clip_a.mp4`, 1280×720, 25 fps,
  H.264 + stereo AAC 44.1 kHz, container duration 30.183 s.
- Current pipeline: shipped `soft.yaml`, forced libx264, one segment/worker,
  no QA inside run, fresh work dir.
- Control: direct libx264 `preset=medium`, `crf=18`, yuv420p + AAC 256k,
  `+faststart`.

Commands and results are recorded here; temporary baseline media was removed after
validation and is not a durable project artifact.

## Baseline results

| Metric | Source | Encode-only control | Current `soft` |
|---|---:|---:|---:|
| Wall time | — | 8.53 s | 19.11 s |
| User CPU | — | 73.89 s | 115.00 s |
| Max RSS reported by `/usr/bin/time -l` | — | 422,686,720 B | 434,864,128 B |
| File size | 8,527,064 B | 8,151,783 B | 8,595,089 B |
| Container duration | 30.183 s | 30.080 s | 30.301 s |
| Video start | 0.000 s | 0.000 s | **1.021 s** |
| Video duration | 30.183 s metadata | 30.080 s | **29.280 s** |
| Decoded video frames | 752 | 752 | **732** |
| Audio start/duration | 0 / 30.000 s | 0 / 30.000 s | **0 / 27.600 s** |
| Audio sample rate | 44.1 kHz | 44.1 kHz | **96 kHz** |
| SAR / DAR | 1:1 / 16:9 | 1:1 / 16:9 | **711:718 / 632:359** |
| VMAF (current QA, raw timeline) | reference | 96.36 | **3.12** |
| SSIM (current QA, raw timeline) | reference | 0.995555 | **0.825338** |
| SSIM after first-frame PTS reset | reference | 0.995555 | 0.898202 |
| PSNR after first-frame PTS reset | reference | 50.92 dB | 29.20 dB |
| Integrated loudness | -18.1 LUFS | -18.1 LUFS | -13.0 LUFS |
| Peak (EBU R128 filter output) | -5.8 dBFS | -5.8 dBFS | -1.4 dBFS |
| pHash similarity | reference | 0.9551 | 0.8301 |

Интерпретация:

- Pipeline `soft` был в 2.24 раза медленнее direct control на этом clip. Это ожидаемо
  частично из-за loudnorm pass и transforms, но текущий benchmark tool не разделяет
  надёжно overlapping phases.
- Низкий raw VMAF нельзя списать только на crop: segment video сдвинут на 1.021 s, а
  финальный `-t` удалил 20 кадров. Даже после reset первой PTS SSIM/PSNR показывают
  сильную разницу; часть её — намеренный crop без spatial registration.
- Главный результат baseline — не quality ranking, а доказательство нарушенного
  media contract: duration, sample rate, frames и SAR/DAR.
- pHash/audio similarity не должны компенсировать эти failures. Correctness gate
  обязан остановить verdict раньше.

## Post-fix candidate results

Один post-fix run на том же synthetic fixture подтверждает correctness regression,
но не является statistical performance baseline (для него нужны 3 cold + 3 warm
runs и natural-content corpus).

| Metric | Broken `soft` 1.3.0 | Fixed `soft` 1.3.3 candidate |
|---|---:|---:|
| Wall time | 19.11 s | 16.27 s |
| User CPU | 115.00 s | 110.37 s |
| Max child RSS (`time -l`) | 434,864,128 B | 435,798,016 B |
| File size | 8,595,089 B | 9,180,346 B |
| Container duration | 30.301 s | 30.080 s |
| Video start / duration | 1.021 / 29.280 s | 0.000 / 30.080 s |
| Decoded video frames | 732 | 752 |
| Audio start / duration | 0 / 27.600 s | 0 / 29.991 s |
| Audio sample rate | 96 kHz | 48 kHz |
| SAR | 711:718 | 1:1 |
| Integrated loudness | -13.0 LUFS | -14.0 LUFS |
| True peak | -1.4 dBFS | -1.7 dBFS |
| Raw unregistered VMAF | 3.12 | 43.34 |
| Raw unregistered SSIM | 0.825338 | 0.940269 |
| pHash similarity | 0.8301 | 0.9557 |

Главный результат — исправленный media contract, а не абсолютное значение raw VMAF.
`crop_resize` меняет spatial registration, поэтому сравнение с непер transformed
source штрафует геометрический сдвиг как потерю качества. До реализации aligned
reference metric это значение нельзя использовать для настройки transform strength.
Текущий QA правильно остаётся RED по своему raw VMAF threshold; это известный P2
measurement limitation, а не основание скрывать результат.

### Target-VMAF feedback regression — 2026-09-03

На real-FFmpeg 4 s fixture профиль `soft` с `target_vmaf=95`, `step=2`,
`max_retries=2` выполнил три encode и получил:

| Attempt | CRF | Raw VMAF against plain source slice |
|---:|---:|---:|
| 0 | 18 | 11.027963 |
| 1 | 16 | 11.060123 |
| 2 | 14 | 11.087632 |

Рост на `0.059669` при шести пунктах CRF доказывает, что loop не управлял
compression-quality loss: score был dominated намеренным crop/rescale относительно
непреобразованного reference. После исправления та же комбинация завершается
`quality.target_vmaf.unregistered_reference` до создания первого `seg_*.mkv`.
Photometric-only `color_eq`/`noise` path не блокируется. Это safety regression, а не
новый corpus quality baseline.

## Отдельные smoke results

| Test | Result |
|---|---|
| Source с 2 chapters → `soft` | Post-fix 2 chapters — PASS |
| Valid custom profile без audio transforms | Post-fix main audio preserved — PASS |
| MKV+SubRip → MP4 | Post-fix `mov_text` subtitle — PASS |
| Divergent windowed audio 125.0 s | Post-fix ≤0.03 s error — PASS |
| Basic 4 s VFR | 90→90 frames, avg 22.5 fps — PASS |
| CFR 23.976/24/25/29.97/30/50/59.94/60 | Multi-segment exact frame count, monotonic matching PTS and AAC-frame A/V bound — PASS |
| Sparse long-GOP, 7 s dynamic fixture | 3 s GOP; exact frame count and correct source-frame match around every concat seam — PASS |
| Non-zero start PTS | MP4 `start_time=5 s`; normalized 0–4 s keyframes and exact 5-segment coverage — PASS |
| A/V internal impulses | Flashes/click impulses at 0.5/3.0/6.5 s stay within one video frame after independent main-audio processing — PASS |
| SSCD direction stub, similarity 0.99 | Direct similarity 0.99 — PASS |
| HDR10 static metadata | x265 preserves ST2086 + MaxCLL/FALL — PASS |
| HDR→SDR | Real `zscale`/tonemap integration — PASS |
| Rubber Band | Real FFmpeg variability integration — PASS |
| SSCD model | Self >0.999; unrelated clip <0.95 — PASS |
| 4K AV1 profiles | Real SVT-AV1/profile integration — PASS |
| VideoToolbox hardware | H.264/HEVC 1080p/4K SDR, HLG Main10, VFR, 2 sessions, cancellation/fallback, `allow_sw=0` — PASS; H.264 uses explicit half-second IDRs; static HDR10 metadata fail-closed |

## Synthetic long-form results — v1.4.0 candidate

Эта matrix проверяет timeline/segmentation/concat/resources, но не заменяет natural
licensed movie corpus. Sources содержат `testsrc2` 320×180, 2 fps и непрерывный
48 kHz tone; профиль `soft`, libx264, 600 s target, 4 workers, QA выключен. Segment
artifacts сохранялись для проверки disk/reuse.

| Duration | Segments | Frames expected/read | Video start/duration | Container drift | Wall | Peak RSS | Source / output / work |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 h | 6 | 7,200 / 7,200 | 0 / 3,600.000 s | +1.646 ms | 283.24 s | 107 MB | 61 / 97 / 94 MB |
| 2 h | 12 | 14,400 / 14,400 | 0 / 7,200.000 s | +7.708 ms | 541.74 s | 136 MB | 128 / 193 / 188 MB |
| 3 h | 18 | 21,600 / 21,600 | 0 / 10,800.000 s | +13.771 ms | 747.82 s | 158 MB | 192 / 289 / 290 MB |

Peak RSS в этой исторической таблице — process maximum из macOS `/usr/bin/time -l`,
не сумма одновременных child RSS. Текущий `tools/benchmark.py` теперь пишет
`rss_method=psutil_process_tree_sum_100ms` и суммирует живые parent/child RSS;
старые числа нельзя напрямую сравнивать с новой серией. Wall scaling близок к линейному.
Основной bottleneck — два последовательных full-duration loudnorm/audio passes;
video workers не могут начать до готовности main audio.

No-op 3 h resume занял `4.24 s`, сохранил тот же output SHA-256
`33925ab7…0fcec0`, состояние осталось `18 done`. В отдельном 1 h video-only crash
test процесс был остановлен после двух completed keyframe-aligned segments; resume
сохранил их SHA-256 и mtime, завершил `6/6`, дал `7200/7200` frames и duration
`3600.000 s` за `18.35 s`.

Дополнительный Mac chaos gate 2026-09-03: `YT_UNIQ_CHAOS_ROUNDS=3` трижды запускал
CLI/FFmpeg в отдельной process group, посылал `SIGKILL` в детерминированные случайные
моменты и затем завершал resume в том же work directory. Итоговый output прошёл
VMAF ≥ 99 относительно чистого fixed-seed baseline; test завершился за `20.82 s`.
Это подтверждает локальный POSIX/synthetic путь, но не имитирует power loss, NFS или
сбой hardware encoder.

Полная phase matrix 2026-09-05 отдельно остановила всю process group через SIGKILL
после probe, plan и первого durable segment, во время main audio, concat и полной
decode validation, а также после durable publication до final validation. Все `7/7` fresh resumes
завершились, полностью декодировали video+audio, сохранили все segment statuses
`done` и точный recorded output path; после добавления exact frame/sample/end-delta
assertions and the pre-validation publication boundary, wall time всей matrix —
`66.14 s` (`7/7 passed`, while the long-form audio benchmark was also active).

## Calibration v2 probe smoke — 2026-09-03

Проверено локально на этом Intel Mac с FFmpeg 9.0.1: synthetic 30 s, 640×360,
30 fps H.264/AAC source; calibration probe budget 6 s распределён по трём
start/middle/end окнам. Cold extraction+concat занял `1.0868 s`, content-keyed warm
reuse — `0.0048 s`. Результат содержит 1 video + 1 audio stream, декодируемая
container duration `6.111 s`, размер `1,561,124 bytes`. Небольшой хвост относительно
6.0 s вызван AAC priming и stream-copy packet boundaries; test gate ограничивает его
и подтверждает, что budget не умножился на число окон. Второй real-FFmpeg regression
заменяет source по тому же path и подтверждает новый probe cache key.

Это smoke инфраструктуры, не quality benchmark: natural licensed footage, VMAF
распределение и выбор similarity thresholds по-прежнему `NOT VERIFIED`.

## Web cross-process admission smoke — 2026-09-03

Проверено локально на APFS/macOS: parent process занял единственный admission slot,
после чего отдельный Python subprocess с другим `run_id` и output name получил
`RunAdmissionFull` (ожидаемый exit code 42). Два независимых FastAPI app state с
общим `output_dir` также дали **429** второму run при capacity 1, а после terminal
release повторный запрос был принят. Fault injection подтверждает удаление
частичного slot при ошибке `fsync`; malformed и foreign-host owners остаются занятыми
fail-closed.

Тест доказывает локальную атомарность и lifecycle admission boundary, но не измеряет
throughput и не квалифицирует NFS, network partition или PID reuse.

## Local resource-budget smoke — 2026-09-03

На APFS/macOS проверены общий encoder slot между parent и отдельным Python
subprocess, cancellable wait и fail-fast при разных capacity одного pool. Disk
registry под mutex допустил ровно одну из двух конкурентных 60-byte reservations при
100 bytes synthetic free, суммировал active bytes, переиспользовал released budget,
reclaimed dead same-host owner и сохранил foreign/malformed owner fail-closed.
Инъекция ошибки второго `fsync` не оставила partial record. После включения budgets
8 real-FFmpeg encode/resume тестов прошли за `155.45 s`. Полный `make check` после
изменения: `1533 passed`, `2 skipped` за `1276.72 s`; CI-equivalent core gate:
`1432 passed`, `1 skipped`, `81.95%` branch-aware coverage. Wheel v1.4.0 собран.
Локальный `linux/amd64` Docker image собран и запущен под UID 1000: `/healthz` и
`/readyz` прошли, registry `/data/work/.resource-admission` доступен для записи.

Повторная resource qualification 2026-09-05 добавила атомарный grow/shrink одного
disk record под тем же cross-process mutex. Рост сверх unreserved free space и смена
owner fail closed, ошибка `os.replace` сохраняет старый record, а shrink немедленно
освобождает admission capacity. Workspace future-byte budget теперь обновляется после
каждого completed segment по максимуму baseline и измеренного bytes/sec, затем падает
до нуля перед concat; final-output reserve также растёт по измеренному encoded
bytes/sec и освобождается после публикации до полной decode validation. Compose
добавляет hard defaults: 4 CPU, 12 GiB RAM, 512 PID и 2 concurrent web runs. Первичный
8 GiB proposal был отклонён после того, как natural 1080p60 HLG raw-VMAF pass достиг
`9,209,112 KiB` RSS (~8.78 GiB) на этом Mac.

## Temporal/SSCD smoke — 2026-09-03

После перевода temporal jitter на ограниченную PTS-сетку real-FFmpeg smoke при
максимальных `0.2/0.2` сохранил `58/72`, `71/90` и `145/180` кадров для 24/30/60 FPS;
VFR-клип с регионами 30/20/60 FPS сохранил исходные классы cadence. SSCD extraction
для 32 одинаковых midpoint samples сформировал byte-identical PNG: прежние 32
процесса — `6.942 s`, один multi-input process — `0.931 s` (`7.45x` на этом Mac;
не general throughput claim). Полный `make check`: `1539 passed`, `2 skipped` за
`1282.79 s`; CI-equivalent core gate: `1433 passed`, `1 skipped`, `82.25%`
branch-aware coverage. Wheel v1.4.0 собран.

Это concurrency/correctness smoke, не throughput benchmark. Natural 4K/1–3 h
bitrate-estimate accuracy, hard disk quotas, mixed GPU visibility, container encode,
containers без общего registry и NFS/network partitions остаются
`NOT VERIFIED`.

## Docker multi-arch correctness smoke — 2026-09-04

На Intel macOS/Docker Desktop с Buildx пройден одинаковый executable smoke для
`linux/amd64` и QEMU-emulated `linux/arm64`. Каждый leg:

1. собрал architecture-native Debian/Python/FFmpeg image;
2. создал 1 s 160×90, 24 FPS H.264/AAC input внутри контейнера;
3. выполнил `yt-uniq run` с `libx264`, реальным segment/concat/final-decode gate;
4. подтвердил H.264 output через `ffprobe`;
5. запустил non-root web runtime и прошёл `/healthz` при Docker health
   state `starting|healthy`.

| Platform | Build | CLI process + bitstream | Web start/health | Result |
|---|---:|---:|---:|---|
| `linux/amd64` | native | PASS | PASS | PASS |
| `linux/arm64` | QEMU on Intel Mac | PASS | PASS | PASS |

On the final snapshot, the changed ARM64 builder and runtime-install layers took
about 243 s and 246 s respectively under QEMU; an unchanged cached rebuild took
3.2 s. Final clip processing took about 3.1 s on amd64 and 17.2 s under ARM64 QEMU.
These are diagnostic timings, not throughput claims. CI now isolates BuildKit caches
per architecture and feeds both into the final manifest build. Native arm64 runner
performance and the published GHCR manifest/attestations remain release-workflow
verification items.

## NFS fault-injection and natural corpus readiness — 2026-09-04

The repository now contains a two-client NFSv4 lab for concurrent lease uniqueness,
partition/reap/stale-commit fencing and crash-journal recovery. Its first Docker
Desktop run used a production-like `hard` mount and wedged the Linux VM during forced
network teardown. The harness now uses a bounded `soft` mount and faults client
TCP/2049 with `iptables`, keeping Docker's control plane responsive. GitHub-hosted
Ubuntu run `33881832592` passed the complete corrected matrix. This qualifies the
application-level ephemeral lab only; `docs/distributed.md` still requires `hard`
mounts in production, and native cross-host deployment qualification remains required.

Повторный локальный запуск 2026-09-05 на Docker Desktop также прошёл: 80/80 unique
leases между двумя NFSv4 clients, TCP/2049 partition + stale reap/resume, crash-journal
recovery, SIGKILL after stage/journal/fence/publish, idempotent second recovery,
malformed checkpoint и bounded-tmpfs ENOSPC. Артефакты сохранены в локальном ignored
`.nfs-qualification/recheck-2026-09-05/`.

`validation-corpus/manifest.example.yaml` plus `tools/natural_corpus.py` validate
relative media paths, explicit owned/licensed/public-domain status, non-empty rights
references, SDR/HDR10/HLG classes and named current/proposed variants. One
`make production-benchmark` run records source SHA-256 and exact Plan provenance,
invokes the existing benchmark and registered-QA pipelines, adds PSNR/LUFS/true peak,
and emits aggregate JSON/CSV/HTML with size/time/RAM deltas. A two-variant real-FFmpeg
synthetic smoke passed locally. No natural media is committed and natural-content
results remain **NOT VERIFIED** until the owner adds licensed fixtures.

## Production benchmark protocol

1. Зафиксировать source checksum, ffmpeg/driver/package versions, profile canonical
   JSON, encoder capability result, seed, command argv и machine metadata.
2. Запускать минимум три cold и три warm repeats. Удалять output/work cache для cold;
   отдельно измерять resume no-op и partial-resume.
3. Сначала проверить decode/stream/timestamp/HDR correctness. Не вычислять composite
   success score для invalid output.
4. Создать encode-only control той же codec family/rate-control/pixel format.
5. Quality metrics считать по temporal/spatial aligned pair и одновременно хранить
   raw end-to-end values.
   Пока aligned reference отсутствует, не включать `target_vmaf` вместе с geometry,
   retiming, mirror, overlays, subtitles или tonemapping: preflight намеренно
   отклоняет такую конфигурацию.
6. Audio LUFS/true peak/sample count измерять после final mux decode.
7. Process-tree CPU/RSS/I/O sampling каждые 0.5–1 s; GPU encoder/utilization/VRAM
   через optional vendor adapter.
8. Для segmentation проверить ±2 s вокруг каждой seam против соответствующего
   source interval; не сравнивать output с самим собой, сдвинутым на кадр.
9. Для long-form зафиксировать peak temporary disk, bytes rewritten, completed
   segments reused после kill, recovery wall time и final checksum/invariants.
10. Хранить per-case JSON artifact и aggregate median/p95; regression gates применять
    только к сопоставимому runner class.

## Предлагаемые release thresholds

Это предлагаемые engineering gates, а не действующие defaults. Численные
quality floors нельзя утвердить по повторам нескольких фрагментов: требуются
human accept/reject labels, независимые held-out titles и отдельные SDR/tonemap
домены. Измеренные диапазоны текущего corpus приведены в начале документа.

| Gate | Initial threshold |
|---|---|
| Decode | ffmpeg/ffprobe success, zero corrupt/decode errors |
| Stream topology | 100% соответствует declared policy |
| Video frame/content | no unintended drop/dup; first/last content present |
| A/V sync | absolute start/end/internal-event delta ≤ 20 ms, one video frame, or one encoded audio frame, whichever larger |
| Audio duration | ≤ one encoded audio frame from expected timeline |
| Loudness | target ±0.5 LU; true peak ≤ configured ceiling + 0.1 dB |
| HDR | required tags + mastering/light metadata preserved; unsupported dynamic HDR rejected |
| Encode-only VMAF/SSIM | NOT VERIFIED: independent per-metric floors require labelled, held-out corpus validation |
| Quality-first derivative VMAF/SSIM | NOT VERIFIED: validate registration and content-class quality before selecting thresholds; no universal 88/95 claim |
| Resource regression | median wall/RSS no worse than 10% without accepted quality/reliability gain |
| Resume | 100% valid completed segments reused; 0 foreign/corrupt artifacts reused |

YouTube рекомендует сохранять исходную частоту кадров, использовать 48 kHz audio и
для stereo upload указывает 384 kbps; актуальные параметры должны проверяться перед
release по [официальным upload settings](https://support.google.com/youtube/answer/1722171?hl=en-GB).

## NOT VERIFIED

- Natural 95-minute A/V, 176-minute video-only and the completed continuous
  180-minute historical baseline are retained. A full v6 three-hour rerender,
  registered long-form quality and human listening/visual acceptance remain unverified.
- 4K long-form throughput/resource usage (короткий 4K AV1 smoke verified).
- HLG и natural HDR corpus; dynamic HDR preservation intentionally unsupported.
- NVENC/QSV/AMF and AV1 VideoToolbox.
- Natural HDR10 static-metadata output on hardware; Intel VideoToolbox is fail-closed
  for this case rather than silently stripping metadata.
- Rubber Band subjective quality on speech/music (functional/duration path verified).
- YouTube ingestion/transcode result.

Эти строки нельзя заменять утверждением «всё работает» до выполнения соответствующей
matrix на подходящем hardware/corpus.
