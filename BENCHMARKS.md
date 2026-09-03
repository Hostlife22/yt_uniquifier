# Benchmark Methodology and Baseline

Baseline: 2026-09-02, commit `14df893`; production acceptance дополнен
2026-09-03. Результаты относятся только к указанному локальному environment и не
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
| Auto encoder policy, H.264 | `quality` → libx264; `speed` → H.264 VideoToolbox | Default follows production quality priority; hardware requires explicit policy/override |
| `soft`, 30.183 s, policy smoke | libx264: 17.86 s, 752 frames; VideoToolbox: 9.36 s, 752 frames; both A/V start 0 | Selection policy changes throughput without losing decoded frames on this Mac |
| Benchmark RSS sampler, 10.01 s fixture | 497,832 KiB peak, `psutil_process_tree_sum_100ms` | New result includes simultaneous Python/FFmpeg RSS; single run, not a regression baseline |

Natural-content viewing/listening, NFS cross-host, NVENC/QSV/AMF и YouTube
ingestion/transcode остаются `NOT VERIFIED`.

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
  SVT-AV1 and VideoToolbox; Python 3.12 `.venv`, package 1.4.0 candidate.
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
