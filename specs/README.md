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
| 14 | [14-audio-cid-resistance.md](./14-audio-cid-resistance.md) | 4 | ⏳ |

Содержит 5 workitem'ов: calibrate quality fallback (VMAF→SSIM→pHash),
rubberband pitch (formant-preserving, дефолт cid_aware 1.012→1.04),
loudnorm target jitter ±LUFS, `audio.compand` (dynamic range jitter),
`audio.reverb` (opt-in в cid_aggressive).

---

## Roadmap дальше (v0.4+ — не план, идеи)

- Multi-GPU dispatch (`CUDA_VISIBLE_DEVICES` round-robin).
- S3 / GCS / Azure Blob backend для очереди.
- HDR10+ / Dolby Vision dynamic metadata propagation.
- Web dashboard для очереди.
- Авто-генерация cid_aware профиля под конкретный контент (ML over corpus).

Image-based subtitles OCR + re-render и multi-language audio rebalance
**вне scope** по решению пользователя.
