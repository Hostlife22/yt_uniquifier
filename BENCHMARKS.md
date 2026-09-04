# Benchmark Methodology and Baseline

Baseline: 2026-09-02, commit `14df893`; production acceptance дополнен
2026-09-04. Результаты относятся только к указанному локальному environment и не
экстраполируются автоматически на фильмы/HDR/GPU.

## Production acceptance delta — 2026-09-03 (post-v1.4.0)

На Intel macOS с Homebrew FFmpeg 9.0.1 дополнительно подтверждено:

| Проверка | Результат | Инженерное решение |
|---|---:|---|
| HLG → `medium_hdr` → libx265 | 100/100 frames, video/audio 4.000 s, `yuv420p10le`, HLG/BT.2020/tv | HLG preserve path разрешён; final validator проверяет полный color contract |
| HLG → HEVC VideoToolbox | 100/100 frames, 4.000 s, `yuv420p10le`, HLG/BT.2020/tv | Hardware HLG подтверждён только для этого Mac |
| 2 × concurrent H.264 VideoToolbox 1080p | 180/180 frames каждый; 7.72 s/job, 8.09 s aggregate wall | `max_parallel=2` подтверждён для этого Mac |
| MP4/MKV/MOV, 3 s tagged SDR | 7/7 integration tests; all A/V streams decode; chapters/subtitles retained by policy | Container smoke закрыт для synthetic core matrix |
| MKV attachment / MOV tmcd / MP4 cover art | Attachment bytes exact; `01:00:00:00` retained; JPEG bytes exact | Supported auxiliary topology is preserved and final-contract validated |
| ASS subtitle → MKV/MOV | ASS copied to MKV; converted to `mov_text` in MOV; language/title retained | Text subtitle policy confirmed; real PGS fixture remains pending |
| APFS queue, 4 processes × 80 jobs | 80 unique leases, 0 duplicates/losses | Single-host atomic lease contract подтверждён; NFS не проверен |
| Segmented VFR, libx264, 6 s | 220/220 frames; 30/20/60 FPS cadence retained; monotonic PTS; A/V end delta ≤50 ms | Software VFR preserve contract разрешён; hardware paths остаются unverified |
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
| Local HEVC/AV1 bitstream matrix | 5/5: libx265, SVT-AV1, libaom-AV1, H.264 and HEVC VideoToolbox; 144/144 frames, tagged BT.709, expected profiles/pixel formats and bounded GOPs | VideoToolbox HEVC profile is pinned to Main/Main10 by output depth; H.264 VideoToolbox produced CABAC/IDR and a one-frame B-run on this Intel Mac; GitHub Apple Silicon produced a three-frame run because VideoToolbox exposes frame reordering as a boolean |
| Strict self-hosted qualification harness | Extended local mandatory selection `h264_videotoolbox,hevc_videotoolbox`: 14 passed, 36 unrequested cases skipped in 122.61 s; JUnit plus 27 hashed/probed media artifacts collected | Bitstream, exact VFR PTS/frame count, HEVC HLG, static-HDR10 fail-closed policy and two-session concurrency passed on this Intel Mac; NVENC/QSV/AMF remain `NOT VERIFIED` |
| Debian 12 production container bitstream matrix | FFmpeg 5.1.9: libx265/SVT-AV1/libaom-AV1 3 passed; two unavailable VideoToolbox cases skipped | Current wheel and software encoder policy work on the shipped Linux runtime, including libaom constant-quality mode |
| HEVC/AV1 two-second GOP synthetic delta | 6 s, 640x360, 24 FPS: keyframes 0/48/96; file-size change vs defaults: x265 +1.48%, SVT +0.96%, libaom +1.06%, HEVC VideoToolbox -3.57% | Small synthetic result supports predictable random access; natural-corpus size/quality impact remains required |
| Multi-audio codec/container matrix | AAC main + Opus secondary across MP4/MOV/MKV | MP4/MOV transcode unsupported passthrough to AAC; MKV preserves Opus and stream metadata |
| SRT/ASS container matrix | Both text formats passed MP4/MOV/MKV; MP4/MOV emit mov_text and MKV retains SubRip/ASS, with language/title intact | Text-subtitle policy is executable rather than documentation-only; real PGS remains pending |
| Exact media deltas | 3 s MP4/MOV/MKV: 72/72 decoded video frames; normalized 48 kHz audio sample delta ≤1024 and packet-end delta ≤50 ms | Found and fixed a real loudnorm PTS discontinuity that previously cut 3581 decoded source-relative samples during final mux limiting |
| Loudnorm mode observability | 44.1 kHz transformed fixture requested linear; FFmpeg reported dynamic; output remained -14.0 LUFS | Runtime mode and fallback reason are now retained instead of assuming measured input guarantees linear processing |
| Seed/resume reproducibility | Fresh invocation seed differs, persisted seed is restored; retained segment/audio mtimes+hashes and decoded A/V SHA-256 remain equal | Resume does not reroll stochastic transforms or reprocess completed media |
| HDR regression | HDR10 preserve, HLG preserve and HDR10→SDR: 4 passed | x265/zscale/tonemap paths remain qualified on this Mac |
| SDR range roundtrip | Full (`pc`) and limited (`tv`) survive segment encode/concat | Generic FFmpeg tags and x264 bitstream range flags agree with decoded output |
| libaom discovery | Generic probe 15.63 s/timeout → probe-only fast settings 1.5–1.9 s/pass | Working libaom is no longer hidden; production CQ arguments are independently exercised by the bitstream matrix |
| Web/plugin security | 57 plugin/web tests plus direct body-limit and SlowAPI regressions | Missing/malformed/negative/duplicate/conflicting/oversize lengths fail closed; sandbox/allowlist/path/rate boundaries are exercised |
| Repository/artifact secrets | Gitleaks 8.30.1: 324 commits / 4.70 MB and local artifacts / 173.81 MB, zero findings | Repository and current build outputs pass the local secret gate |
| Workflow static analysis | actionlint 1.7.12: pass | Release shell snippets use safe artifact discovery/globbing |
| Full local quality gate | 1667 passed, 47 expected hardware/optional skips; Ruff + strict mypy (162 files) pass; 13:02 | RFC #12 implementation and production-hardening changes do not regress the complete Mac suite; skips are unrequested hardware qualification cells |
| CI-equivalent coverage | 1515 passed, 1 expected skipped, 198 deselected; 81.96% branch-aware core coverage | Required 80% gate passes; v1.5.0 wheel and sdist build successfully on the RFC #12 branch |

The no-upscale and raw/registered metric contracts are proposed in GitHub RFC #11
and #12. Implementations are isolated on pending branches and cannot land before the
mandatory comment window and maintainer decision.

RFC #12 implementation-branch synthetic qualification on the current Intel Mac:

| Case | Result | Decision enabled |
|---|---|---|
| Mirror, crop, 1.02× speed, deterministic 10% frame drop | 4/4 real-FFmpeg cases passed; registered SSIM > 0.97 | Exact Plan/segment-seed replay follows spatial and temporal transforms |
| Mirrored H.264 output with local libvmaf | Registered VMAF > 95 | Registered scorer command, local PTS reset and FFV1 reference are operational |
| Audio fixed offset + bounded linear drift | Exact synthetic alignment recovered; low 25% overlap rejected | Offset/drift diagnostic cannot win on a short matching excerpt |
| SSCD monotonic alignment/cache | Identity/adversarial/no-reuse, corrupt-cache recovery and one-hour sparse-grid time bound passed | No output-frame reuse; cache corruption and long-form offset bounds fail safely |

These are deterministic synthetic regressions, not natural-content thresholds. Licensed
speech/music/HDR viewing and listening remains `NOT VERIFIED`.

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
| VideoToolbox hardware | H.264 1080p/4K + HEVC 4K 10-bit, `allow_sw=0` — PASS |

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
network teardown. The harness was corrected to use a bounded `soft` mount for safe
fault-lab cleanup while `docs/distributed.md` continues to require `hard` mounts in
production. The corrected end-to-end result is **NOT VERIFIED on this Mac** until
Docker Desktop recovers; native Linux and the actual deployment mount remain required.

`validation-corpus/manifest.example.yaml` plus `tools/natural_corpus.py` validate
relative media paths, explicit owned/licensed/public-domain status, non-empty rights
references, SDR/HDR10/HLG classes, profiles and encoders. The runner records source
SHA-256 and invokes the existing benchmark and QA pipelines per matrix cell. No media
is committed and no natural-content result is claimed; those results remain
**NOT VERIFIED** until the owner adds licensed fixtures.

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

Это стартовые engineering gates, их нужно утвердить на natural corpus:

| Gate | Initial threshold |
|---|---|
| Decode | ffmpeg/ffprobe success, zero corrupt/decode errors |
| Stream topology | 100% соответствует declared policy |
| Video frame/content | no unintended drop/dup; first/last content present |
| A/V sync | absolute start/end/internal-event delta ≤ 20 ms, one video frame, or one encoded audio frame, whichever larger |
| Audio duration | ≤ one encoded audio frame from expected timeline |
| Loudness | target ±0.5 LU; true peak ≤ configured ceiling + 0.1 dB |
| HDR | required tags + mastering/light metadata preserved; unsupported dynamic HDR rejected |
| Encode-only VMAF | corpus median ≥95, per-clip floor selected by content class |
| Quality-first derivative VMAF | target determined after valid registration; never below 88 without explicit override |
| Resource regression | median wall/RSS no worse than 10% without accepted quality/reliability gain |
| Resume | 100% valid completed segments reused; 0 foreign/corrupt artifacts reused |

YouTube рекомендует сохранять исходную частоту кадров, использовать 48 kHz audio и
для stereo upload указывает 384 kbps; актуальные параметры должны проверяться перед
release по [официальным upload settings](https://support.google.com/youtube/answer/1722171?hl=en-GB).

## NOT VERIFIED

- Real licensed/natural 1 h / 2 h / 3 h+ movies and listening/visual inspection.
- 4K long-form throughput/resource usage (короткий 4K AV1 smoke verified).
- HLG и natural HDR corpus; dynamic HDR preservation intentionally unsupported.
- Real PGS subtitle roundtrip (installed FFmpeg exposes a decoder but no fixture encoder).
- NVENC/QSV/AMF; VideoToolbox concurrency/static HDR metadata.
- Rubber Band subjective quality on speech/music (functional/duration path verified).
- YouTube ingestion/transcode result.

Эти строки нельзя заменять утверждением «всё работает» до выполнения соответствующей
matrix на подходящем hardware/corpus.
