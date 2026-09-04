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

Post-fix verification на этом хосте (обновлено 2026-09-04):

- Ruff и strict mypy: passed (`158` source files).
- Canonical `make check`: `1617 passed, 2 skipped` на полностью установленном
  optional environment (`10:54`, current Phase 6 hardening повтор).
- 30 s `soft`: `752/752` decoded video frames, video start `0.000 s`, audio
  `29.991 s @ 48 kHz`, `SAR 1:1`, loudness `-14.0 LUFS`, chapters/subtitles и
  выбранные audio tracks сохраняются.
- Windowed audio `125.0 s`: duration contract passed с допуском одного AAC frame.
- HDR10 keep-HDR и HDR→SDR с реальным `zscale`: passed; ST2086 и MaxCLL/FALL
  сохранены на libx265. Unsupported dynamic HDR и неопределённый HDR→8-bit policy
  отклоняются до encode; tonemap после другого video transform запрещён.
- SDR limited (`tv`) и full (`pc`) range переживают реальный segment/concat roundtrip;
  FFmpeg tags и x264 bitstream range flags согласованы.
- AAC main + Opus secondary проверены в MP4/MOV/MKV: Opus сохраняется в MKV и
  транскодируется в AAC для MP4/MOV без потери language metadata.
- Loudnorm runtime record содержит requested/reported mode и причину fallback. На
  локальном transformed 44.1 kHz fixture FFmpeg выбрал dynamic при запрошенном
  linear, сохранив итог `-14.0 LUFS`; это теперь наблюдаемое поведение.
- Rubber Band и SSCD с реальной TorchScript model: passed (`5/5`).
- 4K AV1/profile matrix: passed (`11/11`); VideoToolbox hardware smoke прошёл для
  H.264 1080p/4K и HEVC 4K 10-bit без software fallback.
- Synthetic long-form 1/2/3 h: `7200/14400/21600` decoded frames, start `0`,
  duration drift не более `13.8 ms`; peak RSS `107/136/158 MB`.
- Kill/resume: два completed segment сохранили SHA-256 и mtime, итог `7200/7200`
  frames; 3 h no-op resume сохранил output SHA и занял `4.24 s`.
- Fresh resume invocation восстанавливает persisted seed: готовые video/audio
  artifacts сохраняют mtime+hash, а decoded video/audio SHA-256 совпадают точно.
- libx264 H.264 output at 24 FPS подтверждает High Profile, CABAC, max two
  consecutive B-frames and closed 12-frame GOP. Local bitstream matrix additionally
  confirms closed two-second HEVC GOPs for libx265/VideoToolbox and two-second AV1
  GOPs for SVT-AV1/libaom; H.264 VideoToolbox emits High/CABAC/IDR with a one-frame
  B-run on this Intel Mac and a three-frame run on GitHub's Apple Silicon runner.
  A manual trusted self-hosted workflow now retains strict per-device
  JUnit/media/FFprobe evidence, but NVENC/QSV/AMF and AV1 VideoToolbox remain
  `NOT VERIFIED` until that workflow runs on matching physical hardware.
- Segmented software VFR: `220/220` frames across 30/20/60 FPS regions,
  monotonic output PTS, six segment seams and final A/V delta below `50 ms`.
- Software CFR matrix 23.976–60 FPS сохраняет exact frame count и PTS через
  multi-segment concat; synthetic sparse 3-second GOP сохраняет соответствие кадров
  на каждом seam, а три A/V flash/impulse события остаются синхронны.
- Ненулевой MP4 video `start_time=5 s` теперь нормализуется: keyframes `5..9 s`
  становятся относительными `0..4 s`, а план покрывает ровно source duration вместо
  ошибочных 9 секунд. Старый absolute-PTS keyframe cache инвалидируется schema v2.
- Scene planning теперь ограничивает static/sparse gaps target duration и не
  создаёт sub-minimum edge segments; changed segment topology invalidates resume.
- Phase 3 quality regression: 4 s `soft` + `target_vmaf=95` давал raw VMAF
  `11.027963 → 11.060123 → 11.087632` при CRF `18 → 16 → 14`: retries меняли
  compression, но не могли исправить несовмещённую геометрию. Preflight и direct
  segment runtime теперь отклоняют такой feedback до первого encode; photometric-only
  paths остаются доступны.
- Phase 6 hardening: seam tool теперь сравнивает matching source/output windows с
  bounded frame registration; clean fixture проходит, искусственный boundary defect
  отклоняется. Real audio matrix подтверждает сохранение 1/2/6 channels для семи
  effects и обработку 44.1/48/96 kHz с explicit 48 kHz output.
- Web body limit закрывает missing/malformed/negative/duplicate/conflicting/oversize
  Content-Length до route handling; direct SlowAPI, path/symlink, concurrency и
  plugin sandbox tests проходят. Gitleaks не нашёл секретов в 324 commits и ~174 MB
  build artifacts; `actionlint` проходит для всех workflows.
- Generic `libaom-av1` discovery больше не даёт false timeout: probe-only speed args
  сократили локальный smoke с 15.63 s до 1.5–1.9 s. Реальный pipeline test также
  выявил и исправил несовместимое сочетание `-b:v 0` с VBV options в production argv.
- No-upscale и registered QA contract changes вынесены в RFC #11/#12; их код не
  вносится до обязательного comment window и maintainer decision.

Это не означает готовность всей заявленной matrix: real licensed/natural 1–3 h
corpus, NVENC/QSV/AMF, hardware VFR, NFS/network partitions и YouTube
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
| Tests | 226 files |
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

All frontends: output -> media contract -> complete primary-video/all-audio decode
CLI / GUI only: validated output -> optional rich QA report -> JSON/HTML
Web / distributed worker: rich diagnostic QA is not automatic
```

Заданная пользователем схема соответствует коду до `Output`. Final `Validation`
теперь обязателен в общем orchestrator и включает contract плюс полный decode до EOF;
богатый QA остаётся опциональным и не должен заменять correctness gate.

### Карта компонентов

| Component | File / API | Responsibility | Input → Output | Audit result |
|---|---|---|---|---|
| Probe | `core/probe.py::probe` | ffprobe JSON → `SourceMeta` | media path → stream metadata | A/V/S/chapter/HDR plus internal attachment/data/cover-art topology; stable serialized schema retained |
| Profile | `core/profile_loader.py`, `core/models.py::Profile` | YAML validation, transform configs | YAML → typed profile | Единый engine сохранён; часть top-level полей не подключена |
| Plan | `core/orchestrator.py::build_plan` | source/profile/encoder/seed/hash | metadata → `Plan` | Content fingerprint and complete topology participate in resume identity; seed restoration is regression-tested |
| Encoder | `core/encoder.py` | discovery, policy selection and exact-job probe | FFmpeg build + actual plan → candidate/capability | Resolution/pixfmt/color/RC probe and runtime cache invalidation implemented; per-vendor hardware qualification remains open |
| Preflight | `core/preflight.py` | policy/capability checks | `Plan` → findings | Container-aware subtitle/auxiliary/multi-video policy now blocks known lossy mappings; uncommon codecs need fixtures |
| Transform registry | `core/transforms/` | typed filter fragments | labels/params/RNG → filter chain | Хорошо унифицирован; topology guards implemented, perceptual compatibility corpus incomplete |
| Filter graph | `core/pipeline.py` | filter order, mapping, codec args | plan → FFmpeg argv | Shared helpers reduced drift; full-file and segmented execution shapes intentionally remain |
| Segmentation | `core/segmenter.py` | keyframe/scene plans, parallel encode, concat | plan → segments → output | Relative timestamps, bounded scene gaps and final A/V contract verified on software paths |
| Resume | `core/checkpoint.py` | state, hashes, locks | segment state ↔ JSON | Content-bound identity, atomic hashes/locks and exact artifact reuse verified locally; NFS remains open |
| Runner | `core/runner.py` | subprocess, progress, cancel, NVENC retry | argv → events/result | Bounded tail/full log, stall watchdog and process-tree cancel implemented; remote OS qualification incomplete |
| Metadata | `core/metadata.py`, `core/auxiliary_streams.py` | sanitize/reapply metadata and auxiliary policy | source metadata → args | Selected stream/chapter plus supported attachment/timecode/cover metadata preserved and validated |
| QA | `core/qa/` | correctness, pHash, audio, SSCD, VMAF, SSIM, corpus | source/output → report | `INVALID` и независимые UI axes реализованы; aligned quality/structured LUFS schema pending RFC |
| Calibration | `core/calibration/` | scale profile and iterate | source/profile/targets → tuned profile | SSCD direction, deterministic search/cache/retry fixed; natural-corpus thresholds remain experimental |
| CLI | `cli/` | user orchestration | args → core APIs | Core orchestration reused; explicit `--encoder` is strict and cannot silently fall back |
| GUI | `gui/` | desktop orchestration/workers | UI → core APIs | Core не дублируется; реальные heavy e2e opt-in |
| Web | `web/`, `core/output_reservation.py`, `core/resource_budget.py` | API/SSE/static UI, persisted status and shared admission | requests → background threads | Shared run/output/encoder and estimated disk bounds implemented on local FS; hard quotas, device routing, NFS qualification and rich QA policy remain open |
| Distributed | `core/queue`, `cli/cmd_worker.py` | shared-FS lease/worker | pending → done/failed | Process-unique leases and fenced publication recover locally; cross-host/NFS semantics remain open |

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
что main audio обрабатывается целиком. Sample-rate math, global pre-loudnorm
measurement, window overlap и topology guards исправлены; natural-content listening,
phase correlation and clipping qualification остаются открыты.

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
| `audio.pitch_tempo` | Независимые pitch/tempo | Formant artifacts у asetrate; rubberband дороже | N/A | Source-aware math и 44.1/48/96 kHz duration fixed; natural speech/music quality unqualified |
| `audio.eq` | Частотная коррекция | Clipping/tonal coloration | N/A | Pass-1 теперь измеряет pre-loudnorm graph; FFmpeg всё ещё может выбрать dynamic, но requested/reported mode и причина записываются |
| `audio.resample` | Controlled SR conversion | Обычно малая потеря/CPU | N/A | Документация обещает SoX-style, но `soxr` явно не выбран |
| `audio.compand` | Dynamic range | Pumping/changed dialogue dynamics | N/A | Перед loudnorm меняет measurement conditions; channel linking не audited |
| `audio.haas_stereo` | Stereo width | Mono cancellation/comb filtering | N/A | Не channel-layout-aware; опасен для mono/5.1/downmix |
| `audio.spectral_smear` | Малое spectral decorrelation | Transients/clarity хуже | N/A | Вместе с reverb/compand/pitch накапливает слышимую деградацию |
| `audio.reverb` | Room response | Clarity↓, loudness/peak change | N/A | Нужен post-chain loudness measurement и speech/music presets |
| `audio.noise_overlay` | Добавить noise floor | Size почти не меняет, слышимое качество резко хуже | N/A | Default -12 dB ≈ 25% amplitude — неприемлем для quality-first production |
| `audio.loudnorm` | LUFS/true-peak target | Полезен; dynamic mode внутренне может oversample | N/A | Измеряет фактическую preceding chain, final output закреплён на 48 kHz; runtime mode/fallback наблюдаемы |

Критичная комбинация исходного `soft` на 44.1 kHz сокращала звук и отдавала 96 kHz.
Она исправлена source-aware pitch math, измерением фактической pre-loudnorm chain и
явным final 48 kHz. Dynamic fallback остаётся допустимым поведением FFmpeg, но теперь
не является silent: режим и причина сохраняются в log/event.

## Profile audit

Expected VMAF/size/speed ниже обозначены как `UNVERIFIED`, когда у profile нет
измеренного acceptance baseline. Ни один shipped profile не задаёт `target_vmaf`.

| Profile | Video / audio summary | Codec, HDR, use case | Expected VMAF / size / speed | Problems |
|---|---|---|---|---|
| `soft` | crop, color, noise / pitch, loudnorm | H264 SDR, quality-first | UNVERIFIED / near-source / medium | Audio/timestamp defects fixed; natural-content acceptance band absent |
| `medium` | soft + EQ | H264 SDR | UNVERIFIED / ↑ / medium | Pre-loudnorm measurement fixed; compound perceptual quality unqualified |
| `aggressive` | larger crop/color/noise, rotate / pitch, EQ | H264 SDR | UNVERIFIED / ↑ / slow | Несколько resamples; visible quality risk |
| `cid_aware` | crop/color/noise/sharpen/temporal / pitch, EQ, Haas, compand | H264 SDR | UNVERIFIED / ↑↑ / slow | Window drift fixed; natural motion/audio damage and naming claims remain unqualified |
| `cid_aggressive` | above + speed / above + smear/reverb/noise | H264 SDR | UNVERIFIED / ↑↑ / slowest | Explicitly experimental; noise -12 dB and compound quality risk remain |
| `medium_hdr` | crop/color/noise / pitch, EQ, loudnorm | HEVC HDR passthrough | UNVERIFIED / high / slow | Static metadata proven on x265; dynamic HDR rejected; natural/HW matrix incomplete |
| `cid_aware_hdr_to_sdr` | tonemap + crop/color/noise / pitch/EQ/resample | H264 SDR derivative | UNVERIFIED / medium / slow | Synthetic zscale path passes; dark/highlight/skin corpus validation missing |
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

### Первоначально подтверждённые дефекты

Следующие дефекты были воспроизведены реальными smoke-тестами в начале аудита и
исправлены в текущей ветке; актуальный статус и regression evidence перечислены в
`RISK_REGISTER.md`.

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

Локальный FFmpeg-full 9.0.1 содержит `zscale`; real HDR10 keep-HDR, HDR→SDR и HLG
tests прошли. HLG подтверждён на libx265 и HEVC VideoToolbox этого Mac. Natural HDR
corpus и static metadata через остальные hardware encoders остаются **NOT VERIFIED**.

### VFR

Одно- и многосегментный software VFR smoke: **VERIFIED PASS**. Temporal jitter теперь
использует bounded 24 Hz PTS-bucket permutation и прошёл 24/30/60/VFR real-FFmpeg
tests на максимальных параметрах. Но FFmpeg предупреждает, что muxer и
`avoid_negative_ts` могут менять timestamps даже в passthrough mode:
[FFmpeg fps/timestamp documentation](https://ffmpeg.org/ffmpeg.html#toc-Advanced-options).
Hardware encoder cadence остаётся **NOT VERIFIED**.

## QA и similarity audit

Что сделано хорошо: несколько независимых сигналов, graceful optional dependencies,
JSON/HTML output, deterministic sampling, SQLite WAL corpus, explicit availability
notes.

Исправлено в Phase 5:

- plan-aware QA переиспользует final media contract; standalone QA проверяет
  безопасную общую topology. Нарушения дают `INVALID`, а не RED/GREEN.
- first decoded video PTS проверяется отдельно, а primary video и все audio streams
  декодируются до EOF с `-xerror -err_detect explode`, поэтому sampling не скрывает
  повреждённый хвост.
- CLI/HTML показывают correctness, VMAF/SSIM quality и pHash similarity независимо.
- один `analyze_pair` заменил шесть запусков `fpcalc`; для >600 s используются пять
  упорядоченных 120 s окон от начала до хвоста.
- любой числовой VMAF, включая очень низкий, остаётся VMAF; SSIM используется только
  при недоступном VMAF, pHash больше не подменяет quality.

Что остаётся:

- stable `QAReport` пока хранит correctness details в `notes[]`: structured LUFS,
  true peak, decoded frame/sample/end deltas требуют отдельного additive schema RFC.
- SSCD cosine: чем выше, тем изображения более похожи. Calibration теперь передаёт
  direct clamped similarity и требует `mean_similarity <= target`; старые inverted
  cache records отделены schema v2. Threshold всё равно требует authorized corpus.
- `sscd_min` назван «tightest risk», хотя риск высокой похожести задаёт максимум;
  verdict SSCD вообще игнорирует.
- pHash/VMAF/SSIM без geometric/temporal registration смешивают эффект намеренного
  crop/rotate с encode quality. В benchmark raw VMAF упал до 3.12. Для
  `target_vmaf` это теперь fail-fast; post-run aligned QA остаётся незавершённым.
- Legacy Jaccard через sets всё ещё теряет временной порядок внутри покрытых окон;
  Hamming window sequence остаётся доступной отдельно.
- SSCD midpoint seeks теперь объединены в один cancellable runner process на файл;
  temporal alignment, feature cache и явная coverage confidence ещё не реализованы.
- `cid_predict_self` — max эвристик, а не калиброванная вероятность. Его нельзя
  представлять как вероятность реальной внешней системы.

Рекомендуемая семантика: quality/correctness gates отдельно; similarity metrics —
только regression/self-collision diagnostics для разрешённых derivatives. Не нужно
проектировать обход Content ID.

## Calibration audit

Calibration v2 реализован внутри существующего engine без второго pipeline. Общий
временной budget распределяется между opening/middle/end, а short source используется
целиком. Поиск детерминированно измеряет factor 1.0 и bounds 0.25/4.0, затем уточняет
информативные интервалы в logarithmic scale; поэтому он не требует монотонности всего
transform stack. Common seed фиксирован, completed scores атомарно кэшируются по
plan/metric/schema, infrastructure failure повторяется на том же factor и затем
abort-ится. Backend качества pin-ится на первый trial, чтобы VMAF и SSIM не
сравнивались внутри одного поиска. `duration_sec` теперь отражает реальное время.

Result selection feasibility-first: сначала оба независимых gate, затем максимальное
измеренное качество и более мягкий factor. Если feasible point нет, возвращается
lowest normalized violation с явным non-converged result. Ограничение остаётся
экспериментальным: synthetic monotone/non-monotone tests и real-FFmpeg probe не
заменяют calibration thresholds на licensed natural-content corpus.

## Long-form, reliability и performance

- **Исправлено:** runner хранит bounded 2 MiB tail, одновременно пишет полный log,
  использует progress-based stall watchdog и завершает всё process tree.
- **Исправлено:** concat и sanitizer используют общий runner без жёсткого часового
  wall timeout; optional wall timeout остаётся настраиваемым через environment.
- **Исправлено:** первый parallel failure всегда отменяет соседние FFmpeg через
  internal token, даже если caller не передал собственный.
- **Исправлено:** checkpoint ownership использует exclusive create, локальный dead-PID
  recovery и fail-closed cross-host policy; `run_full` освобождает lock на всех путях.
- **Исправлено:** cached `main_audio.m4a` имеет SHA-256/integrity validation и
  публикуется атомарно.
- **Исправлено:** scene segmentation ограничивает gaps через `target_size_sec` и
  отбрасывает tiny edge cuts.
- **Исправлено на общей локальной FS:** web имеет общий для процессов run-count
  admission limit, retention/persisted state, атомарную межпроцессную final-output
  reservation с owner-only release и неблокирующий terminal marker; overlapping
  jobs всех `run_full` frontends делят filesystem encoder slots и estimate-based
  workspace/final-output byte reservations. Hard filesystem quotas, точное per-GPU
  routing, multi-instance state aggregation и NFS qualification остаются открыты.
- **Исправлено для общего correctness gate:** distributed workers используют process-unique
  host+PID+nonce leases, content/plan-stable work paths и fenced staged publication.
  Durable journal с уникальным token-fence автоматически завершает публикацию после
  crash между ownership fence и final rename; old same-name marker не может разрешить
  публикацию, а unfenced staged bytes отбрасываются после reaping.
  Общий `run_full` теперь не публикует результат до успешного полного A/V decode.
  Rich QA sidecars остаются опциональной диагностикой; NFS/network-partition
  qualification остаётся открытой.

## Encoder audit

Availability probing реальным коротким encode — сильная сторона. Но «encoder есть»
не означает «поддерживает данный job»:

| Family | Сейчас | Production gap |
|---|---|---|
| NVENC | H264/HEVC/AV1 candidates, heuristic parallel cap | 10-bit/profile/level/resolution/rate-control/session and per-GPU routing unverified |
| QSV | H264/HEVC/AV1 candidates | Device initialization, 10-bit and filters unverified |
| AMF | H264/HEVC/AV1 candidates | Pixel formats/rate-control/driver capabilities unverified |
| VideoToolbox | Real local H264/HEVC capability probe; HLG and two-session H264 verified on this Mac | Other hardware/driver matrices remain unverified |
| libx264 | Reliable quality-policy default | GOP differs from YouTube guidance |
| libx265 | Reliable quality-policy default | Wider natural HDR corpus still required |
| AV1 | SVT-AV1 locally available | Vulkan auto-selection disabled until a real hardware-frame upload graph exists; other HW AV1 unavailable locally |

Automatic selection now has explicit `quality|balanced|speed` policy, defaulting to
quality; `--encoder` is strict and cannot silently select another candidate. Cache
includes OS/device environment plus NVIDIA UUID/driver state. A later segment encode
failure invalidates the exact-job process cache, forcing the next run to reopen the
encoder in preflight; vendor-specific driver-reset behavior remains `NOT VERIFIED`.

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
- Multi-process web deployment имеет общий run-count admission для одного
  `output_dir` и общий local registry encoder/disk reservations. Это не hard quota:
  bitrate estimate может ошибаться, разные UID/registry paths не координируются, а
  mixed GPU visibility и cross-host semantics не квалифицированы.
- Plugin import происходит до CLI `--no-plugins`; только environment switch даёт
  pre-import disable, что честно указано в help.
- Shared queue доверяет общему filesystem и basename identity; multi-tenant deployment
  требует отдельной trust boundary.

## Documentation drift

- **Исправлено:** declared subtitle/chapter policy теперь подтверждена smoke-тестами,
  а неподдерживаемые topology/container combinations отклоняются до encode.
- `CLAUDE.md` описывает legacy extract→encode, основной path fused.
- **Исправлено:** transform docs синхронизированы с 21 built-in registry ID.
- `docs/bug-triage-2026-05-31.md` упоминается кодом, но файла нет.
- **Исправлено:** FastAPI metadata получает версию из installed package metadata;
  старые `v0.9.0 R*` в module comments обозначают происхождение feature, не runtime.
- Python policy «две последние версии» расходится с CI 3.11/3.12 и unbounded
  `requires-python >=3.11`.
- Profile descriptions заявляют ожидаемый VMAF без воспроизводимого benchmark.

## Проверки

| Gate | Result |
|---|---|
| Full `make check` | 1617 passed, 2 skipped на fully provisioned macOS environment |
| Branch coverage gate | 82.35% (`1472 passed`, required 80%) |
| Ruff | Passed |
| Strict mypy (`158` source files) | Passed |
| Wheel build | v1.4.0 wheel + clean import smoke passed |
| macOS PyInstaller build | Passed: `dist/yt-uniq-gui.app` |
| 16 profile loads | Passed |
| Encoder detection | H264/HEVC VideoToolbox, x264/x265, SVT-AV1 and libaom available locally |
| Chapters smoke | Post-fix passed: 2 → 2 chapters |
| No-audio-transform smoke | Post-fix passed: selected source audio preserved |
| MKV/SRT→MP4 smoke | Post-fix passed: SubRip → mov_text |
| MKV attachment | Post-fix passed: filename/mimetype and extracted bytes preserved |
| MOV timecode | Post-fix passed: tmcd track and `01:00:00:00` preserved |
| MP4 attached picture | Post-fix passed: one program video plus byte-identical JPEG cover |
| ASS subtitle | Post-fix passed: MKV copy and MOV mov_text conversion with language/title |
| Windowed audio 125 s | Post-fix passed: error ≤ 0.03 s |
| 44.1 kHz `soft` smoke | Post-fix passed: 29.991 s, 48 kHz, -14.0 LUFS |
| Timestamp smoke | Post-fix passed: video starts 0.000 s, 752/752 frames |
| Basic VFR smoke | Passed: 90 frames and average FPS preserved |
| HDR10 keep/HDR→SDR/HLG | Passed with FFmpeg-full `zscale`; natural corpus NOT VERIFIED |
| Rubberband path | Passed: real FFmpeg integration |
| SSCD real model | Passed: self-similarity and unrelated-content discrimination |
| 4K | AV1 profile plus H.264/HEVC VideoToolbox smoke passed |
| 1h/2h/3h+ synthetic | Passed with exact decoded frame counts; natural movie corpus NOT VERIFIED |
| Crash/no-op resume | Passed; completed segment/audio bytes+mtime reused, persisted seed restored, decoded A/V SHA-256 equal |

GitHub read-only check на момент завершения аудита: latest CI, docs и CodeQL runs для
`14df893` завершились успешно; open pull requests — 0; open CodeQL alerts — 0.

Подробные приоритеты: `RISK_REGISTER.md`. План локальных исправлений без rewrite:
`PRODUCTION_PLAN.md`. Измерения: `BENCHMARKS.md`.
