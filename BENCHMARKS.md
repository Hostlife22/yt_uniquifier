# Benchmark Methodology and Baseline

Дата: 2026-09-02. Commit: `14df893`. Результаты ниже относятся только к указанному
локальному environment; они не экстраполируются автоматически на фильмы/HDR/GPU.

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
| Peak RSS | Memory limit/container sizing | `RUSAGE_SELF` текущего tool ошибочно исключает FFmpeg child |

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

- macOS x86_64, local FFmpeg 8.1.1.
- Python project environment `.venv`, baseline package 1.3.0; fixed application code
  introduced in 1.3.1 and superseded by 1.3.3 after release-workflow-only fixes.
- Available locally: libx264, libx265, libsvtav1, libvmaf,
  H264/HEVC VideoToolbox.
- Missing locally: `zscale`, `rubberband`, torch/SSCD model.
- Input: existing `tests/fixtures/.gen/clip_a.mp4`, 1280×720, 25 fps,
  H.264 + stereo AAC 44.1 kHz, container duration 30.183 s.
- Current pipeline: shipped `soft.yaml`, forced libx264, one segment/worker,
  no QA inside run, fresh work dir.
- Control: direct libx264 `preset=medium`, `crf=18`, yuv420p + AAC 256k,
  `+faststart`.

Commands and complete temporary artifacts were retained under
`/tmp/ytuniq-prod-bench.7t6cks` for this local session; `/tmp` is not a durable
project artifact.

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

## Отдельные smoke results

| Test | Result |
|---|---|
| Source с 2 chapters → `soft` | Post-fix 2 chapters — PASS |
| Valid custom profile без audio transforms | Post-fix main audio preserved — PASS |
| MKV+SubRip → MP4 | Post-fix `mov_text` subtitle — PASS |
| Divergent windowed audio 125.0 s | Post-fix ≤0.03 s error — PASS |
| Basic 4 s VFR | 90→90 frames, avg 22.5 fps — PASS |
| SSCD direction stub, similarity 0.99 | Direct similarity 0.99 — PASS |

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
| A/V sync | absolute start/end delta ≤ 20 ms or ≤ one video frame, whichever larger |
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

- Real licensed 1 h / 2 h / 3 h+ movies.
- 4K throughput/resource usage.
- HDR10/HLG tonemap и mastering metadata preservation (`zscale` отсутствует).
- NVENC/QSV/AMF/VideoToolbox 10-bit/HDR/4K/concurrency.
- Rubberband quality/duration.
- Real SSCD model execution.
- YouTube ingestion/transcode result.

Эти строки нельзя заменять утверждением «всё работает» до выполнения соответствующей
matrix на подходящем hardware/corpus.
