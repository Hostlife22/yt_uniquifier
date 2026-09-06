# Production Improvement Plan

Основа плана — локальные изменения существующего pipeline. Новый orchestrator,
profile engine или QA system не создаются. План был согласован; Phase 1 и часть
Phase 2/3 production guardrails реализованы в candidate `v1.5.0`. Остальные пункты
сохраняются как проверяемый roadmap, а не как заявление о завершённой поддержке.

## Статус на 2026-09-05

### Дополнение: расширенная квалификация на Intel Mac, 2026-09-06

- Реализован retiming дополнительных аудиодорожек, SRT и chapters через существующий
  concat: 12 реальных MP4/MOV/MKV тестов (×0,5/0,98/1,02/2). ASS/PGS/timed data
  остаются fail-closed; их payload-aware retiming не заявляется завершённым.
- Добавлены streaming frame/sample/PTS diagnostics, sampled disk accounting,
  отдельные QA RAM/time и lossless listening review. Они переиспользуют текущие
  benchmark/QA/core, не создают второй processing pipeline.
- Реальные процессы проверяют общий disk registry и recovery после аварии владельца;
  повторный NFSv4 Docker lab прошёл 80/80 lease и 4 SIGKILL publication boundaries.
- Дополнительно реальные CLI, FastAPI и queue worker одновременно обработали один
  fixture в трёх процессах: наблюдались reservations всех трёх PID в общем registry,
  все output декодируются и reservations освобождены. Это не hard CPU/RAM quota
  для произвольных native процессов; для неё остаются OS/container ограничения.
- Manual release dry-run `34026381605` на `1305a3a` завершён: три GUI платформы,
  AppImage, шесть checksums и семь cosign bundles проверены локально. Docker
  `34027164490` на `eeda84a` прошёл amd64/arm64 без публикации. Последующие
  timestamp-исправления требуют собственного CI/release evidence.
- Расширенные natural 4K/5.1, HDR→SDR и 176/180-minute прогоны сохраняются в
  `validation-corpus/results/extended-*`; окончательные результаты публикуются
  только после их завершения. Timings под общей нагрузкой — не isolated baseline.
- Новый public QA contract вынесен в draft `specs/28-qa-correctness-loudness-rfc.md`:
  до подтверждения владельца поля моделей и CLI не меняются.
- Дополнительный P0 из natural benchmark: main-audio PTS reset терял начальную
  задержку 1,313 с. Исправлены padding до tempo/window split, clock crossfade и
  short-window headroom; реальный 130-секундный тест ×1/×2 прошёл FFmpeg 5/6/9.
  На natural-фрагменте относительный envelope lag уменьшился с −1,32 до −0,01 с;
  human lip-sync/listening и финальная квалификация нового коммита остаются отдельно.
- Code qualification `8cfb11e`: CI `34030418205` (все шесть конфигураций), CodeQL
  `34030418013`, Docker `34030513883` (amd64/arm64, без публикации) и Release
  `34030512436` прошли. Шесть checksums и семь подписей release-кандидата проверены
  локально с привязкой к полному SHA. Release tag не создавался; утверждение QA
  contract, полнота native SBOM и human acceptance по-прежнему не закрыты.

### Предыдущий baseline

QA contract follow-up: owner-approved RFC #21 implemented for v1.6.0 in the
existing report/model/CLI path. Added structured correctness/loudness, opt-in
per-metric gates, local decode-evidence reuse, JSON/CLI snapshots and regression
tests. Natural identity smoke on the retained 4K/5.1 derivative reports full decode
passed, SSIM 1.0 and full-stream -15.70 LUFS / -1.84 dBTP. This is not acceptance of
universal thresholds or a human listening verdict. Earlier draft/pending notes
above describe the preceding audit stage.

- **DONE:** 1.1–1.6; final media contract из 0.1; timeline compatibility guard из
  2.1; stereo-layout guard и layout-aware AAC rates из 3.2; duplicate/concurrency
  web guards; correctness-aware QA verdict; job-specific encoder probe; runner
  watchdog/bounded logs; persistent bounded web lifecycle; filesystem-wide web
  run-count admission; local cross-process encoder/disk admission; per-process queue
  IDs; explicit HDR output-policy/tonemap-order gate; loudnorm runtime-mode
  observability; exact-job encoder-cache invalidation after runtime failure; full
  declared compatibility graph; hierarchical correlation IDs; bounded run-state
  metrics; shared observability redaction; content-addressed corpus cache; RFC #11
  source-aware no-upscale defaults; RFC #12 plan-registered QA diagnostics.
- **PARTIAL:** 2.1, 2.3, 3.1–3.2 — безопасные локальные fixes выполнены, profile
  claims исправлены, destructive stacks помечены experimental, а invalid
  `target_vmaf` feedback и основные compound-quality risks выявляются preflight;
  licensed natural corpus ещё не готов.
- **PARTIAL 2.4:** software CFR 23.976–60 и multi-segment VFR сохраняют decoded
  frames/cadence; non-zero input start PTS normalized; synthetic sparse long-GOP
  seams, A/V impulses and PTS-based temporal jitter verified. H.264/HEVC
  VideoToolbox cadence is qualified on the Intel Mac and hosted Apple Silicon H.264
  passes the explicit IDR contract; NVENC/QSV/AMF and natural long-GOP corpus remain.
- **VERIFIED locally:** HDR10/x265 and HDR→SDR, Rubber Band, real SSCD model, AV1 4K,
  HLG, H.264/HEVC VideoToolbox smoke/concurrency, synthetic 1/2/3 h,
  crash/no-op resume, exact persisted-seed A/V reproducibility, SDR full/limited
  range, MP4/MOV/MKV AAC+Opus policy and APFS distributed fencing.
- **PARTIAL / planned:** public-domain natural 95-minute processing is qualified;
  licensed 2 h/3 h+ corpus and registered full-film metric thresholds remain open,
  NVENC/QSV/AMF, hardware VFR, production cross-host NFS/network partitions and
  YouTube ingestion/transcode.

## Принципы реализации

1. Correctness gates отделены от similarity/quality diagnostics.
2. Любой fix начинается с failing regression test и заканчивается реальным FFmpeg
   smoke там, где затронут media contract.
3. Existing public Profile/Plan/RunEvent contracts сохраняются; breaking schema
   change требует RFC, snapshot update и `CHANGELOG.md`.
4. Один общий stream/container/timestamp policy используется CLI, GUI, web и worker.
5. Для unsupported media выдаётся ранняя понятная ошибка, а не silent degradation.

## Phase 0 — Baseline

### 0.1 Зафиксировать media contracts

- **Files:** `core/models.py`, новый/существующий test helper в `tests/integration`,
  `tests/contracts`, `docs/api-contracts.md`.
- **Current behavior:** output correctness определяется в основном по existence и
  container duration.
- **Problem:** нет формального контракта по streams/timestamps/HDR/chapters.
- **Proposed behavior:** manifest source→expected output: stream count/order,
  language/disposition, start/end duration, frame/sample counts, chapters, color/HDR.
- **Implementation:** расширить probe data и internal `MediaInvariantReport`, не
  ломая public model до RFC.
- **Tests:** SDR/HDR10/HLG, CFR/VFR, 44.1/48/96 kHz, stereo/5.1/multi-audio,
  MP4/MKV/MOV, text/image subtitle fixtures.
- **Risk:** probe fields различаются между FFmpeg versions.
- **Expected result:** machine-verifiable definition «output корректен».

### 0.2 Сделать benchmark воспроизводимым

- **Files:** `tools/benchmark.py`, `tools/perf_compare.py`, `BENCHMARKS.md`, workflows.
- **Current behavior:** wall time + parent RSS + segment count.
- **Problem:** parent RSS исключает FFmpeg; нет quality/audio/size/resource baseline.
- **Proposed behavior:** cold/warm runs, exact command/versions/seed, process-tree
  CPU/RSS, optional GPU telemetry, VMAF/SSIM/PSNR/loudness/true peak/file size.
- **Implementation:** JSON schema v2 additive; resource sampler; explicit cleanup/no-op
  detection; separate encode-only reference.
- **Tests:** schema contract and fake sampler; nightly 30 s fixture, scheduled long run.
- **Risk:** cross-platform resource APIs.
- **Expected result:** решения о speed/quality принимаются по данным, не описаниям.
- **Status:** partial — JSON v1 остаётся обратно совместимым, а `rss_peak_kb` теперь
  агрегирует live parent/FFmpeg tree каждые 100 ms и содержит `rss_method`. CPU/GPU
  telemetry, cold/warm automation и quality/audio integration остаются открыты.

## Phase 1 — Critical fixes

### 1.1 Исправить audio sample-rate/pitch и loudnorm output

- **Files:** `core/transforms/audio_pitch.py`, `core/pipeline.py`, `core/probe.py`,
  `tests/unit/test_audio_pitch.py`, integration audio tests.
- **Current behavior:** `asetrate=48000*pitch` для любого source; output SR implicit.
- **Problem:** 44.1 kHz audio сокращается примерно на 8%; dynamic loudnorm даёт 96 kHz.
- **Proposed behavior:** tempo зависит только от requested tempo, pitch не меняет
  duration; final sample rate 48 kHz по profile/platform policy.
- **Implementation:** передать actual input SR в builder context или использовать
  FFmpeg expression/source-aware filter; сделать final `aresample=48000` после
  loudnorm; измерять фактический pre-loudnorm signal.
- **Tests:** sample-exact duration for 44.1/48/96 kHz, pitch/tempo boundaries,
  silence, loud/quiet, stereo/5.1; assert LUFS/TP/SR.
- **Risk:** изменение публичной plugin build signature; предпочтителен compatible
  context extension.
- **Expected result:** A/V delta < one audio frame; 48 kHz predictable output.

### 1.2 Нормализовать per-segment timestamps

- **Files:** `core/pipeline.py`, `core/segmenter.py`, integration timestamp tests.
- **Current behavior:** copied audio priming determines `avoid_negative_ts` shift.
- **Problem:** video starts at 1.021 s and final `-t` trims frames.
- **Proposed behavior:** каждый encoded video segment starts at 0 independently;
  placeholder streams cannot move video timeline; concat has monotonic PTS.
- **Implementation:** eliminate audio placeholder if concat topology can be normalized,
  otherwise reset mapped streams independently; add `setpts=PTS-STARTPTS` in correct
  location and derive duration from frames/PTS, not container clamp. Validate packet
  timestamps before checkpoint `done`.
- **Tests:** AAC priming/edit-list fixture, negative/non-zero start PTS, B-frames,
  multi-segment CFR/VFR; decoded frame counts and first/last content hashes.
- **Risk:** keyframe seek behavior differs by demuxer.
- **Expected result:** no lost/duplicated frame; start-time ≈0; no hidden tail cut.

### 1.3 Исправить stream/chapter/container mapping

**Status 2026-09-03:** implemented for declared policy. Main/selected audio,
SRT/ASS, chapters, stream metadata, MKV attachments, MOV `tmcd` and MP4 JPEG/PNG
cover art and real PGS-in-MKV are preserved and final-contract validated. PGS fails
before encode for MP4/MOV. Unsupported auxiliary and multiple program-video inputs
fail preflight.

- **Files:** `core/orchestrator.py`, `core/segmenter.py`, `core/metadata.py`,
  `core/preflight.py`, `core/auxiliary_streams.py`, mapping helpers in
  `core/pipeline.py`.
- **Current behavior:** main audio omitted when unfiltered; chapters dropped; all
  subtitles/additional audio copied blindly.
- **Problem:** data loss and late mux failures.
- **Proposed behavior:** one container-aware stream policy controls selection,
  copy/transcode/externalize/reject, metadata and dispositions.
- **Implementation:** pass chapters source; explicit main-audio passthrough; honor
  `Profile.audio_tracks`; MP4 text subtitles→`mov_text`, unsupported image subtitles
  early reject/sidecar policy; codec compatibility table; no absolute/relative audio
  index confusion.
- **Tests:** MP4/MKV/MOV × AAC/Opus/DTS/5.1/multiple languages × SRT/ASS/PGS;
  chapters and default dispositions.
- **Risk:** transcoding subtitle formats can lose styling.
- **Expected result:** preserved declared topology or explicit preflight decision.

### 1.4 Исправить divergent audio overlap

- **Files:** `core/audio_windows.py`, `core/pipeline.py`, tests for audio windows.
- **Current behavior:** adjacent inputs overlap 0.2 s, acrossfade removes 0.1 s.
- **Problem:** +0.1 s per boundary and accumulated content displacement.
- **Proposed behavior:** exact-length output and continuous sample timeline.
- **Implementation:** allocate half-overlap per side or overlap only one boundary;
  derive trims in samples/time base; final exact trim after all latency-aware filters.
- **Tests:** 119.9/120/125 s, 1/2/120 windows, impulses around boundaries, 44.1/48 kHz.
- **Risk:** filter latency (rubberband/reverb) needs measured compensation.
- **Expected result:** length error ≤1 audio frame and no seam click.

### 1.5 Исправить SSCD calibration direction

- **Files:** `core/calibration/loop.py`, `core/qa/sscd.py`, calibration tests/docs.
- **Current behavior:** returns `1 - cosine`, then minimizes it.
- **Problem:** identical pair is considered low collision risk.
- **Proposed behavior:** explicit `similarity` where higher means closer; target and
  UI labels use the same direction.
- **Implementation:** return direct similarity, migrate field naming, invalidate old
  calibration cache/results, correct min/max risk aggregation.
- **Tests:** identical/perturbed/unrelated stub and real-model opt-in suite.
- **Risk:** old saved target meaning changes; document migration.
- **Expected result:** monotonic and semantically correct objective.

### 1.6 Укрепить resume identity и final validation

- **Files:** `core/pipeline.py::compute_plan_hash`, `core/checkpoint.py`, orchestrator.
- **Current behavior:** path+size+rounded duration+basic video metadata.
- **Problem:** changed input/topology can reuse old segments/output/audio.
- **Proposed behavior:** stable cheap content fingerprint plus complete media topology;
  output and main audio manifests are verified.
- **Implementation:** reuse proven head+tail+size+mtime cache fingerprint pattern,
  add stream metadata digest and schema version; SHA/probe main audio/output; atomic
  lock `O_EXCL`; `CheckpointStore` context lifecycle.
- **Tests:** same-size replacement, changed audio/subtitles/chapters/HDR, corrupt
  cached audio/output, concurrent processes, stale cross-host lock.
- **Risk:** existing work dirs invalidated once.
- **Expected result:** resume never combines artifacts from another input/plan.

## Phase 2 — Pipeline correctness

### 2.1 Определить duration/speed policy

- **Files:** video/audio speed transforms, pipeline, segmenter, Profile validation.
- **Current behavior:** video speed and audio tempo independent; final source `-t`.
- **Problem:** slow-down tail is cut, speed-up may leave padding/gaps.
- **Proposed behavior:** default preserve duration/content; explicit retime mode changes
  both streams and expected output duration.
- **Implementation:** cross-field validation; derive expected timeline; remove masking
  clamp; pad/trim only by declared policy.
- **Tests:** 0.5/0.99/1/1.01/2 rates, impulses/start/end hashes, CFR/VFR.
- **Risk:** profile semantics migration.
- **Expected result:** no silent content loss.

### 2.2 Унифицировать FFmpeg command builders

- **Files:** `core/pipeline.py`, `core/segmenter.py`, snapshots.
- **Current behavior:** legacy/full/fused paths use different encoder/mapping policies.
- **Problem:** fixes drift and tests may cover не production path.
- **Proposed behavior:** shared typed helpers for input timeline, stream maps, video tail,
  encoder args and mux policy; two execution shapes remain only where necessary.
- **Implementation:** extract pure argv fragments, no new orchestrator.
- **Tests:** snapshot equivalence across paths/vendors/containers.
- **Risk:** broad diff; perform after P0 regressions lock behavior.
- **Expected result:** one source of truth without rewrite.

### 2.3 HDR/color pipeline

- **Files:** probe/models, `hdr_wrap.py`, `video_tonemap.py`, encoder args, preflight.
- **Current behavior:** basic tags/10-bit only.
- **Problem:** mastering/light/dynamic metadata and encoder capability unknown.
- **Proposed behavior:** explicit passthrough/tonemap/reject decision with verified
  input/output metadata and no silent Dolby Vision/HDR10+ loss.
- **Implementation:** parse side data; preserve ST2086/MaxCLL/FALL where supported;
  tag primaries/transfer/matrix/range explicitly; capability-specific 10-bit probes.
- **Tests:** HDR10/HLG with measured metadata on Linux zscale+x265 and HW runners.
- **Risk:** Dolby Vision/dynamic HDR portability; reject unsupported cases initially.
- **Expected result:** correct HDR or explicit `NOT SUPPORTED`, never accidental SDR.
- **Status:** locally resolved for the software contract — static HDR10/HLG metadata,
  x265 preservation and tonemap fail/allow policies are covered. A checksum-pinned
  natural-scene derivative exposed and now regresses a real YUV-linearisation colour
  cast; the preserve graph uses float planar RGB and explicit BT.2020/10-bit return.
  Native-camera HDR viewing and vendor hardware metadata qualification remain open.

### 2.4 VFR and segmentation correctness

**Status 2026-09-03:** software libx264 CFR/VFR, non-zero start PTS, synthetic
sparse long-GOP seams и internal A/V impulses verified. Keyframe cache schema v2
stores relative timestamps. Hardware encoders and natural-content corpus remain
`NOT VERIFIED`.

- **Files:** probe, segmenter, pipeline, scene_detect.
- **Current behavior:** basic VFR works; complex paths unspecified; scene ignores max.
- **Problem:** timestamp/mux behavior and long static/fast-cut segmentation unstable.
- **Proposed behavior:** preserve VFR by default; optional explicit CFR conversion;
  scene boundaries constrained by min/target/max and keyframes.
- **Implementation:** use avg/r frame rate intentionally, explicit `fps_mode`, packet
  timeline checks; post-process scene candidates.
- **Tests:** mixed cadence/VFR across seams, long GOP/static/rapid cuts.
- **Risk:** FFmpeg-version mux differences.
- **Expected result:** no duplicate/drop except declared temporal transform.

## Phase 3 — Quality

### 3.1 Quality-first profiles

- **Files:** existing YAML profiles, docs, profile validation.
- **Current behavior:** destructive stacks and unverified VMAF descriptions.
- **Problem:** noise/sharpen/rescale/audio stacks amplify second-generation artifacts.
- **Proposed behavior:** `soft` becomes evidence-based quality default; aggressive
  profiles experimental with warnings; platform aliases retain UX.
- **Implementation:** benchmark parameter sweeps on licensed corpus; total-crop semantics;
  no-upscale default; one resampling stage; noise/sharpen content-adaptive bounds.
- **Tests:** profile snapshots/bounds/incompatible combinations plus corpus quality gates.
- **Risk:** similarity diagnostics change; not a production correctness concern.
- **Expected result:** predictable VMAF/size/speed bands backed by reports.
- **Status:** partial — неподтверждённые VMAF/size claims удалены, `aggressive` и
  legacy high-change profiles явно помечены experimental. Parameter tuning и
  acceptance bands ожидают licensed natural corpus. No-upscale contract описан в
  RFC #11 (`specs/26-no-upscale-policy-rfc.md`) и ожидает обязательного согласования.

### 3.2 Audio quality and multichannel policy

- **Files:** audio transforms/pipeline/profiles/preflight.
- **Current behavior:** stereo assumptions, fixed AAC 256k, strong effects stack.
- **Problem:** 5.1/mono phase/downmix risk and insufficient bitrate policy.
- **Proposed behavior:** layout-aware transforms; default preserve 5.1 or encode at
  declared bitrate; effects bypass unsupported layouts.
- **Implementation:** channel masks/metadata; per-layout AAC/Opus rates; post-chain
  LUFS/TP verification; remove noise/reverb/Haas from quality defaults.
- **Tests:** mono/stereo/5.1, downmix correlation, speech/music, clipping/true peak.
- **Risk:** platform/container codec compatibility.
- **Expected result:** audible quality and channel intent preserved.
- **Status:** topology matrix complete locally — Haas fail-closed для mono/5.1;
  EQ/compand/smear/reverb/noise/pitch/resample сохраняют 1/2/6 channels, а
  44.1/48/96 kHz inputs дают explicit 48 kHz output. Natural listening, phase,
  true-peak и clipping corpus остаются `NOT VERIFIED`.

### 3.3 Filter compatibility graph

- **Files:** existing Profile validation/models and transform metadata.
- **Current behavior:** each transform validates params in isolation.
- **Problem:** harmful order/combinations pass validation.
- **Proposed behavior:** constraints for HDR/tonemap, geometry, temporal/speed,
  channel layout and container.
- **Implementation:** declarative capabilities on existing `TransformSpec`, cross-field
  validator/preflight findings.
- **Tests:** pairwise known conflicts and property tests.
- **Risk:** third-party plugin compatibility; unknown capability means conservative warn.
- **Expected result:** bad graph rejected before expensive encode.
- **Status:** complete for declared runtime policy —
  `core/transform_compatibility.py::COMPATIBILITY_GRAPH` inventories HDR, audio,
  temporal, container and registered-quality edges; generic pair/order conflicts
  and third-party `incompatible_with` metadata are evaluated centrally, while
  source/encoder/container-dependent edges delegate to the existing specialised
  preflight probes. The operator reference documents all 21 built-ins. Natural-
  content quality qualification remains separate from graph correctness.

## Phase 4 — Performance

### 4.1 Capability-based encoder selection

- **Files:** `core/encoder.py`, encoder args, cache.
- **Current behavior:** short 8-bit availability probe, hardware-first.
- **Problem:** runtime failure/quality variability and stale cache.
- **Proposed behavior:** `quality|balanced|speed` policy; job-specific capability
  matrix; explicit override either selected or rejected.
- **Implementation:** probe codec/pixfmt/resolution/rate-control/device; include driver
  signature; route per GPU; disable unverified `av1_vulkan` path.
- **Tests:** mocked vendors + self-hosted HW matrix; 10-bit/4K/concurrency.
- **Risk:** probe startup cost, controlled by keyed cache.
- **Expected result:** predictable encoder choice and early incompatibility error.
- **Status:** partial — `quality|balanced|speed` selection implemented with quality
  default; explicit override is strict; job-resolution/pixfmt/rate-control probe and
  NVIDIA-aware cache key are active; a later segment runtime failure invalidates the
  exact-job success so the next preflight reprobes it; unverified `av1_vulkan` is
  disabled. The libx264 H.264 path requests an exact High/CABAC/max-2-B/closed
  half-FPS structure. VideoToolbox requests High/CABAC/frame reordering/closed GOP
  plus explicit half-second IDRs because Apple Silicon can accept `-g` while
  intermittently exceeding it under asynchronous load;
  its device-selected B-frame runs are bounded at one on qualified Intel hardware and
  three on GitHub Apple Silicon. libx265, SVT-AV1, libaom-AV1 and local HEVC
  VideoToolbox have real-output two-second GOP qualification. The internal plan-hash
  revision prevents mixed-policy resume. A manual trusted self-hosted hardware
  workflow now fails closed on explicitly requested encoders and retains bitstream
  evidence. Final run `33966394736` on `6aa0720` passed `22` applicable tests with
  `36` unrequested-vendor skips. Intel VideoToolbox additionally passes SDR/HLG,
  CFR/VFR, 1080p/4K,
  concurrent sessions, cancellation and simulated device-loss software fallback.
  Static HDR10 metadata fails closed. Per-GPU routing and actual NVENC/QSV/AMF plus
  AV1 VideoToolbox device runs remain open.

### 4.2 Resource-aware scheduling

- **Files:** segmenter, web app/routes, GUI workers, distributed worker.
- **Current behavior:** web run count is bounded per shared output directory; all
  `run_full` frontends share local filesystem encoder slots and estimate-based
  workspace/final-output byte reservations.
- **Problem:** oversubscription and disk exhaustion.
- **Proposed behavior:** shared resource budget per process/device and backpressure.
- **Implementation:** bounded job executor, CPU/GPU semaphores, disk reservation,
  queue status and cancellation.
- **Tests:** concurrent jobs/failure/cancel/queue-full chaos tests.
- **Risk:** reduced throughput if defaults too conservative.
- **Expected result:** bounded RAM/disk/session use.
- **Status:** partial — web admission uses atomic exact-owner slots; encoder waits are
  cancellable across local processes; disk records sum remaining workspace and final
  concat estimates per filesystem device and atomically resize workspace capacity
  from measured completed artifacts. A/V estimates include retained audio and the
  reference Compose enforces configurable CPU/RAM/PID ceilings. Dead same-host owners
  are reclaimed and malformed/foreign records fail closed. Native hard filesystem/OS
  quotas, exact GPU routing,
  mixed visibility/UID deployments, distributed backpressure and NFS qualification
  remain open.

### 4.3 Streaming logs and timeouts

- **Files:** runner, sanitizer, concat.
- **Current behavior:** in-memory logs and fixed/unreachable timeouts.
- **Problem:** memory growth, deadlock and indefinite/falsely killed jobs.
- **Proposed behavior:** incremental bounded logs, stall watchdog, phase-specific
  configurable policies.
- **Implementation:** tee to file + ring tail; monotonic last-progress timestamp;
  always terminate process group; expose timeout events.
- **Tests:** silent child, verbose child, grandchild pipe, slow valid job, cancellation.
- **Risk:** platform signal differences.
- **Expected result:** diagnosable long-form operation with bounded memory.
- **Status:** partial — the shared runner has bounded in-memory tail, full log tee,
  progress-based stall watchdog, process-group termination and optional wall timeout;
  concat and sanitizer now use it without a fixed one-hour kill. POSIX process-tree
  cancellation, including a shell grandchild, passed on this Mac; Windows/Linux
  runner qualification and remaining standalone QA subprocess migration stay open.

## Phase 5 — QA

### 5.1 Correctness-first verdict

- **Files:** `core/qa/report.py`, report models/templates, CLI/GUI/web.
- **Current behavior:** visual metrics can produce verdict despite missing streams.
- **Problem:** false GREEN.
- **Proposed behavior:** `INVALID` on topology/timestamp/decode/HDR failure; quality and
  similarity reported as independent axes.
- **Implementation:** run media invariants first; add audio loudness/TP, stream diff,
  frame/sample/end-content checks; no single synthetic «CID probability».
- **Tests:** missing audio/chapters/subtitles, shifted PTS, corrupt tail, HDR tags.
- **Risk:** report schema update requires contract/CHANGELOG/RFC.
- **Expected result:** report answers correctness, quality and similarity separately.
- **Status:** partial — CLI/HTML now expose `INVALID` correctness, independent
  quality and pHash-similarity axes; plan-aware reports reuse the strict stream/HDR/
  timestamp contract and every report decodes primary video/all audio to EOF through
  the shared runner. The stable `QAReport` shape was deliberately not changed without
  an approved schema RFC; structured LUFS/true-peak/frame/sample fields remain open.

### 5.2 Metric validity/performance

- **Files:** qa VMAF/SSIM/pHash/audio_fp/SSCD/corpus.
- **Current behavior:** incomparable fallback, first-600s audio, many FFmpeg processes.
- **Problem:** misleading optimization and slow long-form QA.
- **Proposed behavior:** metric-specific thresholds; encode-only reference/alignment;
  stratified full-duration audio/video; one extraction pass; cached features.
- **Implementation:** no silent fallback under same threshold; source/candidate feature
  cache keyed by content; temporal registration and confidence/coverage reporting.
- **Tests:** known perturbation ladder, shifted/cropped pairs, long synthetic impulses,
  official SSCD model opt-in.
- **Risk:** thresholds corpus-dependent; ship as calibrated diagnostics.
- **Expected result:** metrics correlate with an engineering decision.
- **Status:** implemented in `main` under RFC #12 — existing raw metrics and verdicts
  remain stable; plan-aware FFV1 replay, local PTS reset, bounded audio/SSCD alignment,
  content/tool/plan/seed-keyed SSCD cache and explicit coverage/confidence are locally
  verified. The six-cell native CI matrix passed in run `33966344170` at `6aa0720`;
  licensed natural-content thresholds remain deliberately unverified.

### 5.3 Calibration v2 within current engine

- **Files:** `core/calibration/loop.py`, intensity, CLI/GUI/docs.
- **Current behavior:** deterministic bounded search over a stratified probe.
- **Problem:** source bias and random/non-monotone results.
- **Proposed behavior:** fixed seed/common random numbers, 3–5 stratified clips,
  feasibility-first Pareto search, cached trials and deterministic resume.
- **Implementation:** first test baseline and bounds; evaluate independent correctness,
  quality and diagnostic similarity; stop on plateau/budget/confidence; never convert
  infrastructure failure into a score.
- **Tests:** monotone and non-monotone synthetic objectives, retry/cache/seed/source
  replacement, real corpus study.
- **Risk:** more compute; parallel/cached clips offset cost.
- **Expected result:** reproducible tuned profile with explicit confidence/limitations.
- **Status:** implemented for the current engine — the total probe budget now covers
  start/middle/end, candidates use fixed common random draws, and the search samples
  factor 1 plus bounded endpoints before logarithmic interval refinement. Scored
  trials resume from an atomic plan/metric/schema-keyed cache; result selection is
  feasibility-first and quality-preserving, backend changes abort, elapsed trial time
  is recorded, and failures retry only at the same factor. Monotone/non-monotone,
  cache corruption/reuse, source replacement and real-FFmpeg probe tests are present.
  Confidence thresholds still require the explicitly listed natural-content corpus
  study and are not claimed by the implementation.

## Phase 6 — Production hardening

### 6.1 Long-form recovery qualification

- **Files:** orchestrator/checkpoint/runner tests, `docs/runbook_scale_test.md`.
- **Current behavior:** synthetic 1/2/3 h SDR/libx264 and one interrupted 1 h case are
  retained in `BENCHMARKS.md`; natural long-form and cross-platform recovery remain
  unqualified.
- **Problem:** crash/restart/disk pressure/seams not proven.
- **Proposed behavior:** qualification matrix on 1h/2h/3h+ SDR and HDR.
- **Implementation:** fault injection at probe/segment/audio/concat/final replace; disk
  low/full; kill -9; reboot simulation; exact artifact reuse accounting.
- **Tests:** scheduled self-hosted long-form jobs and retained manifests/reports.
- **Risk:** CI cost.
- **Expected result:** completed segments reused and final output bitstream-correct.
- **Status:** locally resolved, external qualification remains — deterministic regressions cover rejected concurrent
  ownership, initialization cleanup, checkpoint `fsync` failure, atomic main-audio
  failure, concat failure and final replace failure. The POSIX chaos test covers
  process-group SIGKILL/resume at all seven full-pipeline boundaries: after probe,
  plan and segment, during audio, concat and complete validation, and immediately
  after publication before final validation. A fresh invocation restores the persisted seed and
  reuses byte-identical completed video/audio artifacts; decoded A/V hashes match the
  original run exactly. A bounded real ENOSPC tmpfs preserves the last checkpoint;
  the Docker NFSv4 lab passes SIGKILL after stage/journal/fence/publish and idempotent
  resume/publication. Power-loss, natural licensed 1/2/3 h, production hard-NFS and
  hardware HDR cases remain `NOT VERIFIED`.

### 6.2 Web/distributed lifecycle

- **Files:** web state/routes/security, queue leasing, worker.
- **Current behavior:** web terminal state is persisted in a bounded atomic JSON store;
  each final output has an owner-only atomic reservation shared by processes using the
  same filesystem. Queue workers use process-unique leases and fenced staged output.
- **Problem:** local run/encoder/estimated-disk admission is implemented, while hard
  quotas, exact per-device routing, NFS qualification, multi-instance status
  aggregation and network-partition behavior remain open.
- **Proposed behavior:** preserve the current stores and resource boundaries; refine
  per-device identity/remaining-byte estimates only from retained hardware and
  long-form evidence, qualify shared-filesystem semantics, and use the same
  correctness gates in every frontend.
- **Implementation:** existing TTL/count pruning plus `O_CREAT|O_EXCL` run/encoder
  slots, output-owner records and mutex-protected disk byte records; next add explicit
  shared-FS deployment checks. Do not introduce a duplicate job store without
  migration need.
- **Tests:** restart, duplicate name, two workers same host, stale/recovered lease,
  NFS qualification.
- **Risk:** migration of queue layout; version it and retain reader compatibility.
- **Expected result:** deterministic multi-user/multi-worker behavior.
- **Status:** in progress — restart/pruning, two independent app instances, a real
  subprocess run-cap/output conflicts, owner-only release, dead same-host recovery
  and fail-closed foreign/malformed ownership pass locally. Distributed publication
  now has a durable token-fenced journal with real `os._exit` recovery, old-marker
  isolation and unfenced-artifact rejection.
  The shared orchestrator now requires the plan-aware media contract and complete
  primary-video/all-audio decode before any frontend can complete or a queue worker
  can enter its publication fence. Rich similarity/quality reports remain optional
  diagnostics by design. The ephemeral two-client NFSv4 lab now covers partition,
  four SIGKILL boundaries, repeated recovery, corrupt checkpoint and ENOSPC. Hard
  quota enforcement, precise device routing, multi-instance status aggregation and
  real hard-mount/cross-host qualification remain open.

  Web body-size enforcement now fails closed for missing, malformed, negative,
  duplicate, transfer-encoding-conflicting and over-limit Content-Length;
  path/symlink, SlowAPI rate and cross-process admission regressions pass. Production
  docs require TLS reverse proxy and no longer show raw Basic Auth on an untrusted bind.

### 6.3 Supply chain/release

- **Files:** workflows, Docker, pyinstaller, dependency locks, security docs.
- **Current behavior:** strong multi-OS CI, SBOM/signing work, CodeQL currently clear.
- **Problem:** full integration duplicated on six matrix legs; supported Python policy
  unclear; optional binaries vary.
- **Proposed behavior:** fast required matrix + designated media capability runners;
  explicit supported FFmpeg/Python/dependency bounds and artifact smoke.
- **Implementation:** shard tests by value, cache fixtures, publish capability manifest.
- **Tests:** install wheel/app/container, invoke ffmpeg/ffprobe, process canonical clip.
- **Risk:** reducing matrix must not reduce platform coverage.
- **Expected result:** faster feedback with stronger release evidence.
- **Status:** release workflow assembles CycloneDX, verified checksums and per-asset
  keyless cosign bundles during tag and manual runs; only publishing is tag-gated.
  Frozen Linux/macOS/Windows and AppImage versions are checked before upload. Docker
  buildx emits SBOM/provenance and signs the digest. `actionlint`
  passes after shell-safe artifact discovery fixes. Gitleaks found no secrets in 324
  commits or ~174 MB local artifacts. Manual run `33964477926` passed every native
  GUI/AppImage/assembly job at `3ec20ce`; its downloaded candidate passed ZIP,
  SHA-256, CycloneDX and all seven cosign-bundle checks locally. Actual immutable v1.5
  tag assets remain `NOT VERIFIED` because no release tag was created intentionally.
  Final commit `6aa0720` additionally passed six-cell CI `33966344170`, CodeQL
  `33966344225`, hardware qualification `33966394736` and performance regression
  `33966849505`.

## Phase 7 — Documentation

### 7.1 Truthful capability matrix and runbooks

- **Files:** README, CLAUDE, docs architecture/profiles/QA/HDR/web/distributed.
- **Current behavior:** stale architecture/counts and unverified promises.
- **Problem:** operators cannot distinguish verified/pass-through/unsupported.
- **Proposed behavior:** generated version/profile/transform tables; each feature marked
  `VERIFIED`, `LIMITED`, `NOT VERIFIED`, `UNSUPPORTED` by platform.
- **Implementation:** source tables from registry/test manifests; document legal-use,
  similarity diagnostics, recovery and capacity planning.
- **Tests:** link/docs lint, generated-doc drift check.
- **Risk:** none material.
- **Expected result:** documentation matches executable behavior.

## Delivery order and gates

```text
Phase 0 contract
  -> 1.1 audio SR/loudnorm
  -> 1.2 timestamps
  -> 1.3 streams/chapters/subtitles
  -> 1.4 windows
  -> 1.5 SSCD semantics
  -> 1.6 resume identity
  -> Phase 2 correctness
  -> quality/performance/QA
  -> long-form production qualification
```

После каждой задачи: focused regression → related integration/smoke → Ruff → mypy →
full non-visual suite → inspect diff. После каждого phase: `make check`, wheel build,
profile validation и retained benchmark JSON. Production release разрешён только по
`PRODUCTION_CHECKLIST.md`.
