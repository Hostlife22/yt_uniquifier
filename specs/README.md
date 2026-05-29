# yt-uniquifier — Specs Index

Эти спеки декомпозируют roadmap в исполнимые куски. Каждая фаза — отдельный
файл со своим **Goal / Scope / Modules / Acceptance / Tests / Deps**.

## Как читать

- **Goal** — что после фазы будет работать end-to-end.
- **Scope** — что входит и явный «not in scope».
- **Modules** — список файлов с публичными сигнатурами и ответственностью.
- **Acceptance** — наблюдаемые критерии готовности (команды, выходы, метрики).
- **Tests** — обязательные тесты по уровням (unit / integration / smoke).
- **Deps** — какие спеки должны быть завершены до старта этой.

---

## v0.1.0 — Foundation pipeline (`git tag v0.1.0`)

[Мастер-план v0.1](/Users/admin/.claude/plans/snoopy-sprouting-kay.md).

### Граф зависимостей

```
00-bootstrap → 01-probe → 02-transforms → ├─ 03-segmenter ─┐
                                          └─ 04-qa-report ─┴─→ 05-gui-docs
```

### Порядок реализации

| # | Файл | Дни | Можно параллелить | Статус |
|---|------|-----|-------------------|--------|
| 0 | [00-bootstrap.md](./00-bootstrap.md) | 0.5 | — | ✅ |
| 1 | [01-probe-encoder-models.md](./01-probe-encoder-models.md) | 1-2 | — | ✅ |
| 2 | [02-transforms-pipeline-runner.md](./02-transforms-pipeline-runner.md) | 3-4 | — | ✅ |
| 3 | [03-segmenter-resume-metadata-preflight.md](./03-segmenter-resume-metadata-preflight.md) | 2-3 | с #4 | ✅ |
| 4 | [04-qa-report.md](./04-qa-report.md) | 2 | с #3 | ✅ |
| 5 | [05-gui-docs.md](./05-gui-docs.md) | 2 | после #3 и #4 | ✅ |

**Итого v0.1:** 10-13 дней.

---

## v0.2.0 — Real Content ID resistance (`git tag v0.2.0`)

[Мастер-план v0.2](./v0.2-plan.md). **UI/REST API явно вне scope.**

### Граф зависимостей

```
06-real-hdr-pipeline ──┐
                       │
07-audio-strong ───────┼──► 09-calibration ──► 10-scale-validation
                       │
08-fingerprint-qa ─────┘
```

### Порядок реализации

| # | Файл | Дни | Можно параллелить | Статус |
|---|------|-----|-------------------|--------|
| 6 | [06-real-hdr-pipeline.md](./06-real-hdr-pipeline.md) | 1.5 | с 7, 8 | ✅ |
| 7 | [07-audio-strong-variability.md](./07-audio-strong-variability.md) | 2 | с 6, 8 | ✅ |
| 8 | [08-fingerprint-aware-qa.md](./08-fingerprint-aware-qa.md) | 2 | с 6, 7 | ✅ |
| 9 | [09-calibration-loop.md](./09-calibration-loop.md) | 2 | после 7+8 | ✅ |
| 10 | [10-scale-validation.md](./10-scale-validation.md) | 1.5 | после всех | ✅ |

**Итого v0.2:** ~9 дней.

### Ключевые метрики v0.2

| Метрика | Target |
|---|---|
| VMAF input↔output | ≥ 88 |
| pHash similarity input↔output | 0.6–0.85 |
| Chromaprint Jaccard input↔output | < 0.4 |
| CID predicted self-match | < 0.2 |
| HDR roundtrip VMAF | ≥ 90 |
| Variability между двумя runs | chromaprint Jaccard < 0.7 |
| Wall time на 2h 1080p | ≤ 1.5× libx264 baseline |

---

## v0.3 — HDR→SDR · Parallel GPU · Distributed batch

[Мастер-план v0.3](./v0.3-plan.md). **OCR субтитров явно вне scope.**

### Граф зависимостей

```
11-hdr-to-sdr-tonemap   ──┐
                          │
12-parallel-gpu-encoding ─┼── independent — в любом порядке
                          │
13-distributed-batch     ──┘
```

Все три фазы — расширения существующих модулей без перекрёстных
конфликтов. 240 v0.2-тестов остаются зелёными.

### Порядок реализации

| # | Файл | Дни | Можно параллелить | Статус |
|---|------|-----|-------------------|--------|
| 11 | [11-hdr-to-sdr-tonemap.md](./11-hdr-to-sdr-tonemap.md) | 1.5 | с 12, 13 | ⏳ |
| 12 | [12-parallel-gpu-encoding.md](./12-parallel-gpu-encoding.md) | 2 | с 11, 13 | ⏳ |
| 13 | [13-distributed-batch.md](./13-distributed-batch.md) | 2.5 | с 11, 12 | ⏳ |

**Итого v0.3:** ~6 дней.

### Ключевые метрики v0.3

| Метрика | Target |
|---|---|
| HDR→SDR VMAF (tonemap-aware) | ≥ 75 |
| NVENC параллелизм (consumer) | до 3× speed vs sequential |
| Распределённый batch на 4 машинах | ~3.5× throughput одной машины |
| Все unit + integration тесты | 240 + ~30 новых, зелёные |
| ruff + mypy --strict | без issues |

---

## Принципы (стабильные для всех версий)

1. **Ядро без UI и без ffmpeg-специфики.** `Plan` (pydantic) —
   JSON-сериализуемый контракт между слоями.
2. **Один `ffmpeg -filter_complex` per сегмент.** Никакого `bgr24` через
   Python stdin.
3. **Resume через split-process-concat**, не через keyframe-seek.
4. **GUI — тонкая обёртка.** Бизнес-логика только в `core/`.
5. **Тесты на каждый transform** через snapshot сгенерированной строки
   `filter_complex`.
6. **Graceful degradation** для опциональных бинарников (`fpcalc`, `libvmaf`,
   `zscale`, `nvidia-smi`).
7. **Variability by default** (v0.2+): `seed_strategy: per_run`, каждый
   прогон детерминированно-случайный.
8. **No external coordination** (v0.3+): distributed batch через
   shared-FS atomic rename, без redis/db.

## Definition of Done для каждой фазы

- [ ] Все модули фазы реализованы по сигнатурам из спеки.
- [ ] Все тесты в Tests-секции зелёные.
- [ ] CI зелёный (ruff + pytest на ubuntu и macos).
- [ ] Acceptance-команды работают, выход соответствует описанию.
- [ ] README/docs обновлены если фаза меняет CLI.

## v0.3.1 — Audio CID resistance hotfix

Реакция на post-v0.3 анализ против OSS-конкурентов: длинный CID опирается
на аудио сильнее видео; calibrate loop работал на мусорной VMAF-метрике.

| # | Файл | Дни | Статус |
|---|------|-----|--------|
| 14 | [14-audio-cid-resistance.md](./14-audio-cid-resistance.md) | 4 | ✅ |

Содержит 5 workitem'ов: calibrate quality fallback (VMAF→SSIM→pHash),
rubberband pitch (formant-preserving, дефолт cid_aware 1.012→1.04),
loudnorm target jitter ±LUFS, `audio.compand` (dynamic range jitter),
`audio.reverb` (opt-in в cid_aggressive).

---

## v0.5 — Modern PyQt6 desktop UI

[Мастер-план v0.5](./v0.5-plan.md). Полный rewrite GUI shell с 1
функционального экрана (single-file Run в v0.4.x) на 10 экранов,
покрывающих **все 10 CLI команд + v0.4.1 validation harness + inline
profile editor + embedded QA HTML viewer**. Стек тот же (PyQt6),
добавляется `PyQt6-WebEngine` для embedded QA report viewer.

### Граф зависимостей

```
v0.4.x (current)
    │
    ▼
21-gui-foundation (v0.5.0) — app shell, sidebar nav, WorkerBase, AppState, Run screen
    │
    ▼
22-gui-batch-calibrate (v0.5.1) — Batch + Calibrate screens + ChartWidget
    │
    ▼
23-gui-qa-profile-history (v0.5.2) — QA Viewer (QWebEngineView) + Profile Editor + History
    │
    ▼
24-gui-queue-validation (v0.5.3) — Queue dashboard + Validation 3-step wizard (v0.4.1 integration)
    │
    ▼
25-gui-polish-packaging (v0.5.4) — Settings, Corpus, theme switcher, docs/gui.md, PyInstaller
```

### Порядок реализации

| # | Файл | Релиз | Дни | Статус |
|---|------|-------|-----|--------|
| 21 | [21-gui-foundation.md](./21-gui-foundation.md) | v0.5.0 | 3.0 | ⏳ |
| 22 | [22-gui-batch-calibrate.md](./22-gui-batch-calibrate.md) | v0.5.1 | 2.0 | ⏳ |
| 23 | [23-gui-qa-profile-history.md](./23-gui-qa-profile-history.md) | v0.5.2 | 3.0 | ⏳ |
| 24 | [24-gui-queue-validation.md](./24-gui-queue-validation.md) | v0.5.3 | 3.0 | ⏳ |
| 25 | [25-gui-polish-packaging.md](./25-gui-polish-packaging.md) | v0.5.4 | 2.0 | ⏳ |

**Итого v0.5:** ~13 дней (~2 недели).

### Содержание спек

- **Spec 21 (v0.5.0 foundation):** sidebar navigation (QListWidget +
  QStackedWidget), `AppState` + persistence, `theme.py` (dark/light QSS
  tokens), `WorkerBase`, 6 reusable widgets (FilePickerRow,
  EncoderSelector, PreflightPanel, SegmentTimeline, LogConsole,
  KpiPills), Run screen end-to-end (drop file → probe → preflight → run
  → KPI pills). 9 placeholders для остальных screens.
- **Spec 22 (v0.5.1):** Batch screen с per-file table (status / progress
  / output_path), Calibrate screen с live convergence chart (3 lines:
  intensity_factor / self_match / quality), `ChartWidget` с QtCharts +
  QPainter fallback.
- **Spec 23 (v0.5.2):** QA Viewer с embedded `QWebEngineView` (+
  graceful fallback на "Open in browser" если WebEngine не установлен),
  Profile Editor с auto-form generation per pydantic schema (все 18
  transforms), History screen с persistence в `~/.config/yt_uniquifier/
  history.json`. Worker hooks для history entries.
- **Spec 24 (v0.5.3):** Queue dashboard с 2 sub-tabs (Queue management
  + Worker control), `QueueStatusWorker` polling каждые 2s, `QueueWorker`
  drainer. Validation 3-step wizard (Generate / Record / Analyze)
  интегрирует v0.4.1 harness (`tools/generate_variants.py` +
  `validation_log.csv` + `tools/validation_correlate.py`).
- **Spec 25 (v0.5.4):** Settings screen с theme switcher (live re-apply
  без перезапуска), Corpus screen, `docs/gui.md` с screenshots,
  PyInstaller spec для desktop binaries (с fallback на `pipx install`
  documentation если cross-compile fails).

### Ключевые метрики v0.5

| KPI | До v0.5 | После v0.5.4 |
|---|---|---|
| % CLI команд покрытых в GUI | 10 % (1 / 10) | **100 % (10 / 10)** + Validation wizard |
| Time-to-first-run новому пользователю | ≥ 5 min (CLI learning curve) | **≤ 60s** (drop file → Run) |
| Все v0.4.x core тесты | passing | passing (нет регрессий) |
| Новые GUI тесты | 0 | **~80** (widget + worker + screen smoke) |
| Headless launch test | n/a | `QT_QPA_PLATFORM=offscreen yt-uniq-gui` clean exit |
| Theme switching | n/a | **dark / light / system без перезапуска** |
| Cross-platform | only via pipx | **PyInstaller .app / .exe / AppImage** + pipx fallback |

### Что НЕ входит в v0.5

| Идея | Почему deferred |
|---|---|
| Web/Electron/Tauri shell | Пользователь явно выбрал PyQt6 |
| Mobile / web hosting | Desktop-only tool |
| Multi-user / auth / cloud sync | Single-user local |
| Real-time CID API integration | Нет API; Validation wizard остаётся manual record loop |
| Automated YouTube upload | TOS risk |
| Plugin system для custom transforms | Transforms self-register через core/transforms/ |
| Code signing (.app / .exe) | Defer to v0.6 если user demand justifies |
| App auto-update (Sparkle / WinSparkle) | v0.6 candidate |
| Localization (i18n) | English-only |

---

## v0.4 — Empirical-grounded uniqueness

[Мастер-план v0.4](./v0.4-plan.md). Реакция на пост-v0.3.3 honest audit:
закрывает 6 верифицируемых пробелов (placebo transforms, deterministic
periodicity, encoder signature) и впервые открывает **самый большой
remaining hole** — отсутствие эмпирической валидации против реального
Content ID.

### Граф зависимостей

```
v0.3.3 (current)
    │
    ▼
17-quick-wins (v0.4.0)
    │
    ▼
18-real-cid-validation-harness (v0.4.1) — производит ≥5 samples
    │
    ▼
19-per-segment-audio-divergence (v0.4.2) — opt: ship только если v0.4.1
    │                                          показал что audio uniformity
    │                                          реально предсказывает match
    ▼
20-bitstream-sanitization (v0.4.3) — opt-in, не зависит строго от 19
```

### Порядок реализации

| # | Файл | Релиз | Дни | Статус |
|---|------|-------|-----|--------|
| 17 | [17-quick-wins-and-truly-random-jitter.md](./17-quick-wins-and-truly-random-jitter.md) | v0.4.0 | 1.0 | ⏳ |
| 18 | [18-real-cid-validation-harness.md](./18-real-cid-validation-harness.md) | v0.4.1 | 0.5 | ⏳ |
| 19 | [19-per-segment-audio-divergence.md](./19-per-segment-audio-divergence.md) | v0.4.2 | 1.0 | ⏳ |
| 20 | [20-bitstream-sanitization.md](./20-bitstream-sanitization.md) | v0.4.3 | 0.5 | ⏳ |

**Итого v0.4:** ~3 дня.

### Содержание спек

- **Spec 17 (v0.4.0):** 5 быстрых фиксов — убрать `-metadata
  encoder=yt-uniquifier`, выключить placebo `audio.resample 47999↔48000`,
  поднять weak defaults (crop max_strength, color_eq brightness/saturation,
  noise strength, audio.eq jitter), переписать `temporal_jitter` на
  Poisson-sampled frame list (период 60 s вместо 30-кадровой регулярности),
  новый `video.subpixel_sharpen` через `unsharp=lx=5:la=0.05`.
- **Spec 18 (v0.4.1):** `tools/generate_variants.py` + `validation_log.csv`
  schema + `tools/validation_correlate.py` (Spearman, без scipy) +
  `docs/validation_harness.md`. Бридж между предиктором и реальностью
  — нужен ручной upload loop.
- **Spec 19 (v0.4.2):** window-split audio chain под `seed_strategy:
  divergent`. Каждое 60 s окно получает свой seed через
  `derive_segment_seed(plan_hash, idx, run_seed)`, между окнами
  `acrossfade=d=0.1`. Loudnorm остаётся global. Новый KPI
  `audio_fp_hamming_variance` ≥ 4 bits.
- **Spec 20 (v0.4.3):** opt-in `--sanitize-bitstream` flag — второй pass
  через libx264 CRF 20 чтобы стереть NVENC/QSV/AMF/VideoToolbox
  bitstream signatures. Audio stream-copy. HDR/HEVC paths защищены через
  explicit reject.

### Ключевые метрики v0.4

| KPI | До v0.4 | После v0.4.0 | После v0.4.2 | Источник |
|---|---|---|---|---|
| pHash similarity (mean) | < 0.75 | **< 0.70** | < 0.70 | Stronger crop/noise + subpixel + Poisson temporal |
| pHash worst chunk | < 0.80 | **< 0.75** | < 0.75 | То же |
| VMAF mean | ≥ 85 | **≥ 83** | ≥ 83 | Slight relax — accept stronger transforms cost |
| Audio FP Hamming/frame | ≥ 15 bits | ≥ 15 bits | **≥ 18 bits** | Per-window seed divergence widens distribution |
| Per-window audio Hamming variance | n/a | n/a | **≥ 4 bits** | Новый KPI — Spec 19 |
| File metadata = generic ffmpeg | no | **yes** | yes | Spec 17 — strip encoder=yt-uniquifier |
| Real-CID no-match rate (own content, N≥5) | not measured | not measured | **measured** | Spec 18 — ручной upload loop |

### Что НЕ входит в v0.4

| Идея | Почему deferred |
|---|---|
| Neural FP attack mode (differentiable Chromaprint surrogate) | 1-2 месяца research, нужен GPU + dataset. v0.5+ |
| Калибровка на community CID model | Нет открытой CID-class модели (Meta VSC2022 closest, но не CID-equivalent) |
| Автоматизированный YouTube upload | TOS risk; YouTube Studio Copyright tab visibility только в UI |
| Verify Smitelli 2010 thresholds | Закрывается косвенно через Spec 18 sample data |

---

## v0.3.2 + v0.3.3 — Доказуемая CID-устойчивость

[Мастер-план](./v0.3.2-3-plan.md). Реакция на пост-v0.3.1 ресерч: cross-check
с независимым отчётом выявил критическую дыру (`cid_aware.pitch = 1.04`
внутри документированного Smitelli match-zone ±5%) и четыре измеримых
пробела с верифицированными академическими источниками
(Smitelli 2010, Fojcik & Syga arXiv:2501.11171 2025).

### Граф зависимостей

```
v0.3.1 (current) ─► 15-pitch-haas-hotfix ─► 16-temporal-jitter-and-divergence
                    (v0.3.2)                 (v0.3.3)
```

v0.3.3 строго зависит от v0.3.2: bumped pitch — это baseline, поверх
которого ложится temporal jitter и остальное.

### Порядок реализации

| # | Файл | Релиз | Дни | Статус |
|---|------|-------|-----|--------|
| 15 | [15-pitch-haas-hotfix.md](./15-pitch-haas-hotfix.md) | v0.3.2 | 1 | ⏳ |
| 16 | [16-temporal-jitter-and-divergence.md](./16-temporal-jitter-and-divergence.md) | v0.3.3 | 4 | ⏳ |

**Итого v0.3.2 + v0.3.3:** 5 дней.

### Содержание спек

- **Spec 15 (v0.3.2 hotfix):** bump `cid_aware.pitch` 1.04→1.06, bump
  `cid_aggressive.pitch` 1.06→1.08 (выход за Smitelli ±5% match-threshold);
  новый transform `audio.haas_stereo` (15–25 ms delay одного канала,
  mono-compatible вариант phase inversion); обновление `docs/profiles.md`
  с цитатой Smitelli 2010.

- **Spec 16 (v0.3.3 main):** `video.temporal_jitter` (random blackout / dup
  / drop по Fojcik 2025 — 60%+ μAP drop в VSC2022); audio FP Hamming delta
  как явный KPI в `qa.json` и HTML-отчёте; `seed_strategy: divergent`
  (per-segment seed = hash(plan_hash, segment_idx)); `audio.noise_overlay`
  (parametric pink/white noise mix через `anoisesrc + amix`).

### Ключевые метрики v0.3.3 (target после релиза)

| Метрика | До v0.3.2 | После v0.3.3 | Источник target |
|---|---|---|---|
| `cid_aware.pitch` | 1.04 (in match zone) | 1.06 (above threshold) | Smitelli 2010 |
| pHash mean | ~0.78 | < 0.70 | Fojcik 2025 |
| pHash worst chunk | 0.91 | < 0.80 | Fojcik 2025 |
| Audio FP Hamming/frame | не виден | ≥ 30 bits | chromaprint heuristic |
| Per-segment cross-pHash | n/a | < 0.85 | Fojcik 2025 |
| VMAF mean | ~92 | ≥ 85 | acceptable trade-off |

### Что НЕ входит (discredited research)

Watermark overlay; adversarial gradient attacks (требуют CID API);
`-map_metadata -1` как primary technique; GOP/PTS jitter; reverse audio;
AB-style frame interleaving 60/120/240fps (платформа коллапсирует);
face morphing; mobile-app "anti-CID" features (mythological).

---

## Roadmap дальше (v0.4+ — не план, идеи)

- Multi-GPU dispatch (`CUDA_VISIBLE_DEVICES` round-robin).
- S3 / GCS / Azure Blob backend для очереди.
- HDR10+ / Dolby Vision dynamic metadata propagation.
- Web dashboard для очереди.
- Авто-генерация cid_aware профиля под конкретный контент (ML over corpus).

Image-based subtitles OCR + re-render и multi-language audio rebalance
**вне scope** по решению пользователя.
