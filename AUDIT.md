# Production Audit: yt_uniquifier

Дата аудита: 2026-09-02
Базовый коммит: `2c8d677` (`v1.3.3`, `main`)
Режим аудита: исходный production-код не изменялся до согласования плана.
Implementation status обновлён после подтверждения пользователя.

## Implementation status — v1.4.0 candidate

Подтверждённые P0 и локально исправимые P1 correctness defects устранены в
существующем pipeline без rewrite. Добавлены container-aware stream mapping,
zero-based video segments, source-aware audio processing, обязательный final media
contract, content-bound resume identity, atomic checkpoint/audio handling и
воспроизводимая calibration. В v1.4.0 дополнительно добавлены job-specific encoder
probe, bounded streaming FFmpeg logs и stall watchdog, static HDR10 metadata
contract, stream title/disposition validation, bounded persistent web run store и
уникальная identity каждого queue-worker процесса.

Post-fix verification на этом хосте:

- Ruff и strict mypy: passed (`155` source files).
- Canonical `make check`: `1361 passed, 2 skipped` на полностью установленном
  optional environment (финальный повтор после fixes).
- 30 s `soft`: `752/752` decoded video frames, video start `0.000 s`, audio
  `29.991 s @ 48 kHz`, `SAR 1:1`, loudness `-14.0 LUFS`, chapters/subtitles и
  выбранные audio tracks сохраняются.
- Windowed audio `125.0 s`: duration contract passed с допуском одного AAC frame.
- HDR10 keep-HDR и HDR→SDR с реальным `zscale`: passed; ST2086 и MaxCLL/FALL
  сохранены на libx265. Unsupported dynamic HDR отклоняется до encode.
- Rubber Band и SSCD с реальной TorchScript model: passed (`5/5`).
- 4K AV1/profile matrix: passed (`11/11`); VideoToolbox hardware smoke прошёл для
  H.264 1080p/4K и HEVC 4K 10-bit без software fallback.
- Synthetic long-form 1/2/3 h: `7200/14400/21600` decoded frames, start `0`,
  duration drift не более `13.8 ms`; peak RSS `107/136/158 MB`.
- Kill/resume: два completed segment сохранили SHA-256 и mtime, итог `7200/7200`
  frames; 3 h no-op resume сохранил output SHA и занял `4.24 s`.

Это не означает готовность всей заявленной matrix: HLG, real licensed/natural
1–3 h corpus, NVENC/QSV/AMF, VideoToolbox concurrency, multi-segment VFR и YouTube
ingestion/transcode остаются `NOT VERIFIED`. Synthetic и unit tests нельзя выдавать
за доказательство этих платформенных сценариев.

## Executive summary

Проект не требует переписывания. В нём уже есть сильное typed core, единая модель
профиля, детерминированные seeds, atomic output/checkpoints, encoder probing,
preflight, CLI/GUI/web, distributed queue и обширная test matrix. Эти части следует
сохранить и укрепить локальными исправлениями.

Однако текущий release нельзя считать production-ready для длинного видео. Аудит
подтвердил несколько дефектов уровня P0/P1:

1. На штатном 44.1 kHz fixture профиль `soft` сократил audio с 30.000 до 27.600 s и
   вывел его как 96 kHz. `audio.pitch_tempo` вычисляет `asetrate` от заданных 48 kHz,
   а не от реальной частоты входа.
2. AAC priming/edit-list timestamps смещают video сегмента на 1.021 s; финальный
   `-t` затем обрезает 20 кадров. QA показал VMAF 3.12 против 96.36 у контрольного
   encode.
3. Профиль без audio transforms теряет основной/единственный audio stream.
4. Chapters всегда удаляются основным orchestrator path.
5. MKV с SRT падает при выводе MP4 из-за безусловного `-c:s copy`.
6. Windowed audio увеличивается на 0.1 s на каждую границу; для 2 h это около
   11.9 s сдвига содержимого до принудительного обрезания хвоста.
7. SSCD objective в calibration инвертирован: почти идентичный результат считается
   выполнением цели низкого self-match.
8. Resume identity не является content identity и допускает повторное использование
   checkpoint/output после подмены входного файла с совпавшими path/size/duration.

Приоритет проекта должен измениться с «изменить метрики similarity» на доказуемую
сохранность содержимого, stream topology, timestamps, HDR и качества. pHash,
Chromaprint и SSCD полезны только как внутренние diagnostic/regression signals, а не
как доказательство поведения YouTube Content ID.

## Scope и метод

Изучены `README.md`, `CLAUDE.md`, `AGENTS.md`, `CHANGELOG.md`, документы в `docs/`,
история требований в `specs/`, `.claude/plans/`, локальные reference-инструкции в
`.claude/skills/`, весь `src/`, тестовые suites, workflows, profiles и packaging.
Фактические команды FFmpeg проверялись по builders и реальными smoke runs.

Размер области аудита:

| Область | Файлы / объём |
|---|---:|
| `src/yt_uniquifier/core` | 77 Python files |
| GUI | 47 files |
| Web | 14 files |
| Tests | 224 files |
| Docs | 30 documents, около 4.9k строк |
| Specs | 37 documents, около 14.3k строк |
| Shipped profiles | 16 YAML |
| Registered transforms | 20 (`9 audio + 11 video`), плюс internal HDR wrapper |

Авторитетными источниками для ожидаемого поведения были код и исполненная команда,
а не README/spec. Например, README обещает сохранение chapters/subtitles, но
фактический concat этого не обеспечивает.

## Фактическая архитектура

```text
CLI / GUI / Web / Worker
          |
          v
probe.py -> profile_loader.py -> orchestrator.build_plan()
                                  | encoder.detect/select
                                  | seed + plan_hash
                                  v
                              preflight.py
                                  v
segmenter.plan_segments() -> CheckpointStore
          |                       |
          +--> fused segment video encode (pipeline.py)
          |      video filters + copied audio/subtitle placeholders
          |
          +--> full-source main audio encode
                 normal or divergent 60 s windows
                                  |
                                  v
                         concat_segments()
                                  |
                         optional sanitizer
                                  v
                         atomic final output

CLI / GUI only: output -> QA report -> JSON/HTML
Web / distributed worker: QA is not automatic
```

Заданная пользователем схема соответствует коду до `Output`, но `QA` и
`Validation` не являются обязательными pipeline stages.

### Карта компонентов

| Component | File / API | Responsibility | Input → Output | Audit result |
|---|---|---|---|---|
| Probe | `core/probe.py::probe` | ffprobe JSON → `SourceMeta` | media path → stream metadata | Хорошая defensive parsing; недостаточно HDR/timestamp/topology metadata |
| Profile | `core/profile_loader.py`, `core/models.py::Profile` | YAML validation, transform configs | YAML → typed profile | Единый engine сохранён; часть top-level полей не подключена |
| Plan | `core/orchestrator.py::build_plan` | source/profile/encoder/seed/hash | metadata → `Plan` | Hash не удостоверяет содержимое |
| Encoder | `core/encoder.py` | availability smoke and selection | FFmpeg build → candidate | Проверяет запуск, но только 640×360 8-bit |
| Preflight | `core/preflight.py` | policy/capability checks | `Plan` → findings | Полезен; не проверяет mux compatibility/topology и все stream tracks |
| Transform registry | `core/transforms/` | typed filter fragments | labels/params/RNG → filter chain | Хорошо унифицирован; есть ошибки параметризации и channel-awareness |
| Filter graph | `core/pipeline.py` | filter order, mapping, codec args | plan → FFmpeg argv | Основной segmented и legacy full-file paths расходятся |
| Segmentation | `core/segmenter.py` | keyframe/scene plans, parallel encode, concat | plan → segments → output | Fused path экономит I/O; timestamp/stream/timeout defects |
| Resume | `core/checkpoint.py` | state, hashes, locks | segment state ↔ JSON | Atomic segment hashes сильны; identity/locking/final validation слабы |
| Runner | `core/runner.py` | subprocess, progress, cancel, NVENC retry | argv → events/result | Process-tree cancel хорош; нет stall/wall timeout, logs держатся в RAM |
| Metadata | `core/metadata.py` | sanitize/reapply metadata | source metadata → args | Language partial; chapters/subtitle dispositions/attachments теряются |
| QA | `core/qa/` | pHash, audio, SSCD, VMAF, SSIM, corpus | source/output → report | Богатая диагностика; verdict неполон и метрики плохо сопоставлены |
| Calibration | `core/calibration/` | scale profile and iterate | source/profile/targets → tuned profile | Математически неустойчива, SSCD objective ошибочен |
| CLI | `cli/` | user orchestration | args → core APIs | Полное покрытие функций; `--encoder Force` фактически preference |
| GUI | `gui/` | desktop orchestration/workers | UI → core APIs | Core не дублируется; реальные heavy e2e opt-in |
| Web | `web/` | API/SSE/static UI | requests → background threads | Нет global scheduling/persistence/QA/pruning |
| Distributed | `core/queue`, `cli/cmd_worker.py` | shared-FS lease/worker | pending → done/failed | Atomic rename хорош; heartbeat только per-host и resume не переносим |

## Что уже реализовано и не следует дублировать

### Video

Реализованы crop+resize, fit/crop/pad aspect, rotation, mirror, blend с B-video,
color EQ, noise, subpixel sharpen, temporal drop/flash, speed, subtitle burn-in,
HDR passthrough wrapping и HDR→SDR tonemap. Базовый VFR smoke сохранил 90 кадров,
`r_frame_rate=30`, `avg_frame_rate=22.5`; следовательно, VFR нельзя объявлять
полностью сломанным. Нужна матрица сегментированного/HW/VFR, а не второй pipeline.

### Audio

Реализованы pitch/tempo (`asetrate` и optional rubberband), EQ, resample, compand,
Haas, spectral smear, reverb, noise overlay и EBU R128 loudnorm. Архитектурно верно,
что main audio обрабатывается целиком; исправлять нужно sample-rate math, global
measurement placement, windows и channel-layout behavior.

### Pipeline / QA / infrastructure

- Keyframe и scene segmentation, parallel CPU segments, resume, checksums, retry,
  shared-FS worker queue и atomic final replace уже существуют.
- pHash, Chromaprint/Jaccard/Hamming, SSCD, VMAF, SSIM, corpus SQLite, calibration,
  JSON/HTML reports уже существуют.
- Реализованы detection для NVENC/QSV/AMF/VideoToolbox/x264/x265/SVT-AV1 и других
  AV1 candidates, Docker, FastAPI, PyQt6 и Typer CLI.
- Test architecture включает unit, integration, GUI, smoke, contract, property и
  visual scopes; core coverage gate — 80%.

## Transform audit

`HDR` означает совместимость только при корректной color-management ветке; сейчас
полная сохранность HDR metadata не доказана. `VFR` означает, что сам filter не
требует CFR, но итог зависит от timestamps/muxer.

| Transform | Назначение | Влияние quality/size/time | HDR / VFR | Комбинации и риск |
|---|---|---|---|---|
| `video.crop_resize` | Незаметное геометрическое изменение | Потеря края и дополнительный resample; размер непредсказуем; medium cost | HDR risky / VFR yes | Четыре стороны выбираются независимо: `0.06` может удалить до 12% размера по оси; sharpen усиливает ringing |
| `video.fit_aspect` | Platform canvas | Crop теряет content, pad сохраняет; upscale дорогой | HDR risky / VFR yes | Без no-upscale policy 1080/4K profiles раздувают low-res source; YouTube умеет принимать исходный aspect |
| `video.rotate` | Малый поворот | Двойная интерполяция+crop, мягкость углов | HDR risky / VFR yes | После crop/scale усиливает resampling; fill color должен быть color-aware |
| `video.mirror` | Horizontal flip | Семантически сильное изменение, text становится неверным | Technically / yes | Должен оставаться opt-in; не quality-first default |
| `video.blend_b` | Смешение второго video | Ghosting, сильный bitrate рост | Unverified / alignment-sensitive | Нужны timebase/FPS/size normalization и rights guardrails |
| `video.color_eq` | Brightness/contrast/gamma/saturation | Banding/clipping при нескольких операциях | No proof / yes | HDR transfer нельзя обрабатывать как SDR gamma; noise+contrast усиливают shadows |
| `video.noise` | Dither/grain | Маскирует banding при малом уровне, повышает bitrate | Unverified / yes | Sharpen+noise создают ringing и ухудшают повторный YouTube encode |
| `video.subpixel_sharpen` | Вернуть резкость после scale | Halo/ringing, bitrate↑, CPU↑ | Unverified / yes | Применять один раз после resize, strength по resolution/content |
| `video.temporal_jitter` | Drop/gray flash frames | Motion judder/flash, content loss | Unverified / risky | Hardcoded 1440-frame cadence не равно 60 s при 30/60/VFR; `blackout_blur` фактически gray flash |
| `video.speed` | Изменение временной шкалы | Motion cadence и duration меняются | pixels yes / timestamps risky | `rate<1` + final source `-t` обрезает хвост; нет cross-check с audio tempo |
| `video.subtitles` | Burn-in SRT | Permanent pixels, encode cost | Unverified / yes | Отдельно от soft subtitle preservation; filter injection protection сделана хорошо |
| `video.tonemap_sdr` | HDR→SDR | Необратимая смена dynamic range; high cost | Converts HDR / VFR yes | Требует verified zscale, scene/content validation, BT.709 tagging |
| `audio.pitch_tempo` | Независимые pitch/tempo | Formant artifacts у asetrate; rubberband дороже | N/A | **P0:** assumes 48 kHz; 44.1 kHz source стал 27.6 s; sync ломается |
| `audio.eq` | Частотная коррекция | Clipping/tonal coloration | N/A | Pass-1 loudnorm измеряется до EQ, поэтому linear mode может silently fall back |
| `audio.resample` | Controlled SR conversion | Обычно малая потеря/CPU | N/A | Документация обещает SoX-style, но `soxr` явно не выбран |
| `audio.compand` | Dynamic range | Pumping/changed dialogue dynamics | N/A | Перед loudnorm меняет measurement conditions; channel linking не audited |
| `audio.haas_stereo` | Stereo width | Mono cancellation/comb filtering | N/A | Не channel-layout-aware; опасен для mono/5.1/downmix |
| `audio.spectral_smear` | Малое spectral decorrelation | Transients/clarity хуже | N/A | Вместе с reverb/compand/pitch накапливает слышимую деградацию |
| `audio.reverb` | Room response | Clarity↓, loudness/peak change | N/A | Нужен post-chain loudness measurement и speech/music presets |
| `audio.noise_overlay` | Добавить noise floor | Size почти не меняет, слышимое качество резко хуже | N/A | Default -12 dB ≈ 25% amplitude — неприемлем для quality-first production |
| `audio.loudnorm` | LUFS/true-peak target | Полезен; dynamic mode может resample до 192 kHz | N/A | Measurement сейчас до preceding transforms; output SR не закреплён и стал 96 kHz |

Критичная комбинация текущего `soft` на 44.1 kHz: `pitch_tempo` сокращает звук,
после чего loudnorm переходит в dynamic mode и AAC output становится 96 kHz. Это не
«агрессивный профиль», а shipped default с фактической A/V ошибкой.

## Profile audit

Expected VMAF/size/speed ниже обозначены как `UNVERIFIED`, когда у profile нет
измеренного acceptance baseline. Ни один shipped profile не задаёт `target_vmaf`.

| Profile | Video / audio summary | Codec, HDR, use case | Expected VMAF / size / speed | Problems |
|---|---|---|---|---|
| `soft` | crop, color, noise / pitch, loudnorm | H264 SDR, quality-first | UNVERIFIED / near-source / medium | 44.1 kHz P0; measured VMAF 3.12 из-за timestamp path |
| `medium` | soft + EQ | H264 SDR | UNVERIFIED / ↑ / medium | То же + loudnorm pre-measure mismatch |
| `aggressive` | larger crop/color/noise, rotate / pitch, EQ | H264 SDR | UNVERIFIED / ↑ / slow | Несколько resamples; visible quality risk |
| `cid_aware` | crop/color/noise/sharpen/temporal / pitch, EQ, Haas, compand | H264 SDR | UNVERIFIED / ↑↑ / slow | Divergent window drift; motion/audio damage; naming claims не доказаны |
| `cid_aggressive` | above + speed / above + smear/reverb/noise | H264 SDR | UNVERIFIED / ↑↑ / slowest | Quality-first use case не соответствует; noise -12 dB; tail truncation risk |
| `medium_hdr` | crop/color/noise / pitch, EQ, loudnorm | HEVC HDR passthrough | UNVERIFIED / high / slow | Mastering/CLL/dynamic metadata preservation не доказано |
| `cid_aware_hdr_to_sdr` | tonemap + crop/color/noise / pitch/EQ/resample | H264 SDR derivative | UNVERIFIED / medium / slow | zscale unavailable locally; real HDR validation missing |
| `youtube_1080p` | fit 1080 + soft/medium | H264 YouTube | UNVERIFIED / medium / medium | Может upscale; no explicit GOP/profile/audio 384k recommendation |
| `youtube_4k` | fit 4K + soft | H264 YouTube 4K | UNVERIFIED / very high / very slow | Arbitrary upscale; H264 4K CPU cost; no evidence against source-resolution upload |
| `youtube_av1` | fit 1080 + medium | AV1 YouTube | UNVERIFIED / low / very slow | HW AV1 paths incompletely parameterized; compatibility matrix absent |
| `youtube_4k_av1` | fit 4K + soft | AV1 4K | UNVERIFIED / high / slowest | Same, plus upscale cost |
| `youtube_shorts` | vertical fit + medium | H264 9:16 | UNVERIFIED / medium / medium | Crop may remove subject; needs safe-zone/content preview |
| `instagram_reels` | vertical fit + medium | H264 9:16 | UNVERIFIED / medium / medium | Top-level -16 LUFS ignored; actual loudnorm -14 |
| `instagram_square` | square fit + medium | H264 1:1 | UNVERIFIED / medium / medium | Top-level -16 ignored; duplicated platform parameters |
| `tiktok_vertical` | vertical fit + medium | H264 9:16 | UNVERIFIED / medium / medium | Top-level -16 ignored; near-duplicate |
| `linkedin_square` | square fit + soft | H264 1:1 | UNVERIFIED / medium / medium | Top-level -16 ignored; near-duplicate |

Profiles с одинаковыми transform shapes следует оставить как user-facing aliases,
но вынести общие presets/constraints или хотя бы добавить generated validation,
чтобы magic numbers не расходились. Удалять aliases не требуется.

## FFmpeg и stream correctness

### Подтверждённые дефекты

- `pipeline.py:653-656` использует `-avoid_negative_ts make_zero` на сегменте, где
  copied AAC начинается с `-1.021315`. Это сдвигает video с 0 до 1.021 s.
  `segmenter.py:804-805` затем обрезает mux по container duration. На fixture:
  segment имел 752 video frames, final — 732.
- `pipeline.py:97-99` строит `asetrate=48000*pitch` независимо от source 44100 Hz.
- `video_geom.py:45-52` после crop+scale не нормализует SAR. В smoke source
  `SAR=1:1, DAR=16:9` стал `SAR=711:718, DAR=632:359`, то есть плеер получает
  геометрически искажённое изображение при тех же 1280×720 stored pixels.
- `pipeline.py:746` и `:903` не фиксируют output sample rate. Документация FFmpeg
  указывает, что dynamic loudnorm upsample-ит до 192 kHz и рекомендует явно задать
  `-ar`/`aresample`; AAC в smoke стал 96 kHz.
- `orchestrator.py:628-636` не передаёт `map_chapters_from`; concat удаляет chapters
  через `segmenter.py:796`.
- `pipeline.py:704-709` возвращает пустой main-audio command без audio transforms,
  но concat не мапит `0:a:0`, когда `main_audio is None`.
- `segmenter.py:790-791` stream-copy-ит subtitle codec в любой requested extension;
  SubRip→MP4 завершился ошибкой muxer.
- `pipeline.py:829-860` расширяет обе стороны окна на 0.1 s, но acrossfade вычитает
  только 0.1 s: отрендеренные 125.0 s стали 125.1 s.
- `video.speed` меняет timeline, а unconditional final `-t source.duration` скрывает
  рассогласование и обрезает slow-down tail.

### HDR

Probe v1.4.0 хранит transfer/primaries/matrix/range/bit depth, ST2086 mastering
display и MaxCLL/MaxFALL. Job-specific encoder probe использует фактические
resolution/pixel format/rate-control/color args. Static HDR10 метаданные передаются
через x265 params и валидируются после mux; HDR10+/Dolby Vision ранне отклоняются,
поскольку их сохранность не доказана. YouTube требует корректные PQ/HLG, Rec.2020
primaries и matrix, а для PQ рекомендует SMPTE ST 2086 и MaxCLL/MaxFALL metadata:
[официальные HDR требования YouTube](https://support.google.com/youtube/answer/7126552?hl=en).

Локальный FFmpeg-full 9.0.1 содержит `zscale`; real HDR10 keep-HDR и HDR→SDR tests
прошли. HLG, natural HDR corpus и static metadata через hardware encoder:
**NOT VERIFIED** (hardware static-HDR path намеренно rejected до доказательства).

### VFR

Базовый односегментный VFR smoke: **VERIFIED PASS**. Но FFmpeg предупреждает, что
muxer и `avoid_negative_ts` могут менять timestamps даже в passthrough mode:
[FFmpeg fps/timestamp documentation](https://ffmpeg.org/ffmpeg.html#toc-Advanced-options).
Segmented VFR, temporal jitter, hardware encoders и concat: **NOT VERIFIED**.

## QA и similarity audit

Что сделано хорошо: несколько независимых сигналов, graceful optional dependencies,
JSON/HTML output, deterministic sampling, SQLite WAL corpus, explicit availability
notes.

Что создаёт ложную уверенность:

- `report.py::verdict` не учитывает наличие/число audio/subtitles/chapters, audio
  similarity, SSCD, loudness и true peak. Output без audio/chapters может быть GREEN.
- `duration_match` с допуском 0.5 s проверяет container duration, поэтому не видит
  content shift/tail truncation, замаскированные `-t`.
- SSCD cosine: чем выше, тем изображения более похожи. Официальная реализация Meta
  прямо задаёт это направление и приводит `>0.75` как пример copy threshold:
  [SSCD official repository](https://github.com/facebookresearch/sscd-copy-detection).
  Calibration возвращает `1-similarity`, но затем требует это значение `<= target`,
  то есть награждает сходство.
- `sscd_min` назван «tightest risk», хотя риск высокой похожести задаёт максимум;
  verdict SSCD вообще игнорирует.
- pHash/VMAF/SSIM без geometric/temporal registration смешивают эффект намеренного
  crop/rotate с encode quality. В benchmark raw VMAF упал до 3.12.
- Chromaprint ограничен первыми 600 s, затем его fingerprint искусственно делится по
  всей длительности фильма. Jaccard через sets теряет временной порядок.
- SSCD default 32 pair вызывает отдельный FFmpeg process на каждый frame каждой
  стороны вместо одного extraction pass.
- `cid_predict_self` — max эвристик, а не калиброванная вероятность. Его нельзя
  представлять как вероятность реальной внешней системы.

Рекомендуемая семантика: quality/correctness gates отдельно; similarity metrics —
только regression/self-collision diagnostics для разрешённых derivatives. Не нужно
проектировать обход Content ID.

## Calibration audit

Текущий algorithm не является bisection: factor умножается на 1.5 или делится на
1.3 без bracket и без проверки монотонности. У каждой итерации новый random seed,
поэтому сравниваются одновременно intensity и иные random draws. Первый prefix clip
не представляет фильм, а `work_dir/test_clip.mp4` переиспользуется для другого
source только по наличию файла. Ошибка encode трактуется как `self_match=1` и ведёт к
ещё большей aggressiveness. Если ни один candidate не проходит обе цели, `_better`
выбирает наименьший similarity даже при разрушенном качестве.

`quality_score` переключает VMAF → SSIM×100 → pHash×100 с одним числовым threshold,
хотя шкалы не эквивалентны; VMAF ≤10 объявляется ненадёжным, что может скрыть реально
сломанный output. `CalibrationStep.duration_sec` всегда равен 0.

Production replacement должен остаться внутри существующего calibration engine:
fixed seed/common random numbers, content-stratified clips, cached evaluations,
explicit failure state и constrained multi-objective/Pareto selection. Сначала нужно
исправить correctness и метрики; тюнинг поверх сломанного pipeline бессмысленен.

## Long-form, reliability и performance

- Runner хранит весь FFmpeg output в `list[str]` и пишет log после завершения;
  long-form run расходует память и теряет журнал при crash.
- Основной merged-stdout read loop не имеет stall/wall timeout. Ветка 3600 s для
  real process недостижима, поскольку stderr merged в stdout.
- Первый parallel failure отменяет соседние FFmpeg только если caller передал
  `CancelToken`; иначе executor ждёт оставшиеся segment jobs.
- Concat имеет жёсткий 3600 s timeout; sanitizer также 3600 s и держит PIPE до
  `communicate`, что создаёт deadlock/false timeout risk для длинного encode.
- Checkpoint lock реализован check-then-replace, а не exclusive create: два процесса
  могут одновременно решить, что lock свободен. Cross-host lock сразу reclaim.
- `CheckpointStore.close()` предусмотрен, но `run_full` его не вызывает; daemon
  освобождает lock только при exit.
- `main_audio.m4a` resume проверяется лишь по существованию path, без hash/probe.
- Scene segmentation игнорирует `target_size_sec` как upper bound: static фильм может
  стать одним гигантским segment, fast-cut — множеством малых.
- Web запускает unbounded background threads, каждый до 64 segment workers; нет
  global CPU/GPU budget. Одинаковый `output_name` даёт last-writer-wins race; registry
  не очищается и не переживает restart; queue sentinel может блокировать worker.
- Distributed heartbeat per hostname не различает два worker process на host;
  живой sibling маскирует погибший job. При lease на другой host меняется input path
  и plan hash, поэтому resume не переносится. Distributed worker отключает
  `target_vmaf` и не запускает QA.

## Encoder audit

Availability probing реальным коротким encode — сильная сторона. Но «encoder есть»
не означает «поддерживает данный job»:

| Family | Сейчас | Production gap |
|---|---|---|
| NVENC | H264/HEVC/AV1 candidates, heuristic parallel cap | 10-bit/profile/level/resolution/rate-control/session and per-GPU routing unverified |
| QSV | H264/HEVC/AV1 candidates | Device initialization, 10-bit and filters unverified |
| AMF | H264/HEVC/AV1 candidates | Pixel formats/rate-control/driver capabilities unverified |
| VideoToolbox | Real local H264/HEVC probe works | Quality args differ between paths; 10-bit HDR and concurrency unverified |
| libx264 | Reliable fallback | Current default hardware-first conflicts with quality-first priority; GOP differs from YouTube guidance |
| libx265 | Reliable availability | Mastering/CLL propagation not implemented |
| AV1 | SVT-AV1 locally available | `av1_vulkan` lacks vendor-specific args; HW AV1 matrix absent |

`--encoder` описан как Force, но selection фактически применяет override только среди
candidates требуемого profile codec и может молча выбрать другой. Cache живёт 7 дней
и keyed по FFmpeg version, но не по GPU/driver state.

## Security findings

Недавние CodeQL findings исправлены; открытых alerts после предыдущего push — 0.
Path validation и plugin capability/audit-hook defenses выглядят осмысленно.

Оставшиеся operational risks:

- Hash-locked base/dev/GUI dependency set: `pip-audit` reports 0 known
  vulnerabilities after raising Click/Pillow/cryptography bounds. Fully provisioned
  Intel macOS `[ml]` still reports 22 records against the platform-limited Torch
  2.2.2; only the SHA-256-pinned official SSCD model is qualified there.
- Bandit reports 0 High findings. Five Medium `urlopen` findings were reviewed:
  updater/marketplace/SSCD enforce HTTPS (SSCD additionally pins SHA-256), while the
  notification webhook is an operator-configured destination and remains a
  deployment trust-boundary concern.
- Web может быть поднят на `0.0.0.0` без auth; rate limiting не заменяет auth/TLS.
- Unbounded run concurrency — denial-of-service для CPU/RAM/disk/GPU.
- Plugin import происходит до CLI `--no-plugins`; только environment switch даёт
  pre-import disable, что честно указано в help.
- Shared queue доверяет общему filesystem и basename identity; multi-tenant deployment
  требует отдельной trust boundary.

## Documentation drift

- README обещает сохранение soft subtitles/chapters, что опровергнуто smoke tests.
- `CLAUDE.md` описывает legacy extract→encode, основной path fused.
- Docs указывают 19 transforms; registry содержит 20.
- `docs/bug-triage-2026-05-31.md` упоминается кодом, но файла нет.
- Web reports version `0.9.0`, package — `1.3.0`.
- Python policy «две последние версии» расходится с CI 3.11/3.12 и unbounded
  `requires-python >=3.11`.
- Profile descriptions заявляют ожидаемый VMAF без воспроизводимого benchmark.

## Проверки

| Gate | Result |
|---|---|
| Full `make check` | 1361 passed, 2 skipped на fully provisioned macOS environment |
| Ruff | Passed |
| Strict mypy (`155` source files) | Passed |
| Wheel build | v1.4.0 wheel + clean import smoke passed |
| macOS PyInstaller build | Passed: `dist/yt-uniq-gui.app` |
| 16 profile loads | Passed |
| Encoder detection | H264/HEVC VideoToolbox, x264/x265, SVT-AV1 available locally |
| Chapters smoke | Post-fix passed: 2 → 2 chapters |
| No-audio-transform smoke | Post-fix passed: selected source audio preserved |
| MKV/SRT→MP4 smoke | Post-fix passed: SubRip → mov_text |
| Windowed audio 125 s | Post-fix passed: error ≤ 0.03 s |
| 44.1 kHz `soft` smoke | Post-fix passed: 29.991 s, 48 kHz, -14.0 LUFS |
| Timestamp smoke | Post-fix passed: video starts 0.000 s, 752/752 frames |
| Basic VFR smoke | Passed: 90 frames and average FPS preserved |
| HDR10 keep/HDR→SDR | Passed with FFmpeg-full `zscale`; HLG/natural corpus NOT VERIFIED |
| Rubberband path | Passed: real FFmpeg integration |
| SSCD real model | Passed: self-similarity and unrelated-content discrimination |
| 4K | AV1 profile plus H.264/HEVC VideoToolbox smoke passed |
| 1h/2h/3h+ synthetic | Passed with exact decoded frame counts; natural movie corpus NOT VERIFIED |
| Crash/no-op resume | Passed; completed segment bytes/mtime and final SHA reused |

GitHub read-only check на момент завершения аудита: latest CI, docs и CodeQL runs для
`14df893` завершились успешно; open pull requests — 0; open CodeQL alerts — 0.

Подробные приоритеты: `RISK_REGISTER.md`. План локальных исправлений без rewrite:
`PRODUCTION_PLAN.md`. Измерения: `BENCHMARKS.md`.
