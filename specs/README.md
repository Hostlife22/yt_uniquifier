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

## v0.1.0 — Foundation pipeline (релиз готов)

[Мастер-план v0.1](/Users/admin/.claude/plans/snoopy-sprouting-kay.md).

### Граф зависимостей

```
00-bootstrap
   │
   ▼
01-probe-encoder-models
   │
   ▼
02-transforms-pipeline-runner
   │
   ├──────────────┬─────────────┐
   ▼              ▼             ▼
03-segmenter   04-qa-report   (parallel-safe)
   │              │
   └──────┬───────┘
          ▼
       05-gui-docs
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

**Итого v0.1:** 10-13 дней. Закрыто в `git tag v0.1.0`.

---

## v0.2 — Real Content ID resistance

[Мастер-план v0.2](./v0.2-plan.md). **UI/REST API явно вне scope.**

### Граф зависимостей

```
06-real-hdr-pipeline ──┐
                       │
07-audio-strong ───────┼──► 09-calibration ──► 10-scale-validation
                       │
08-fingerprint-qa ─────┘
```

Фазы 06/07/08 независимы и параллелятся.
09 требует 07+08. 10 требует все предыдущие.

### Порядок реализации

| # | Файл | Дни | Можно параллелить | Статус |
|---|------|-----|-------------------|--------|
| 6 | [06-real-hdr-pipeline.md](./06-real-hdr-pipeline.md) | 1.5 | с 7, 8 | ⏳ |
| 7 | [07-audio-strong-variability.md](./07-audio-strong-variability.md) | 2 | с 6, 8 | ⏳ |
| 8 | [08-fingerprint-aware-qa.md](./08-fingerprint-aware-qa.md) | 2 | с 6, 7 | ⏳ |
| 9 | [09-calibration-loop.md](./09-calibration-loop.md) | 2 | после 7+8 | ⏳ |
| 10 | [10-scale-validation.md](./10-scale-validation.md) | 1.5 | после всех | ⏳ |

**Итого v0.2:** ~9 дней.

### Ключевые метрики v0.2 (после прохождения всех фаз)

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
   `zscale`).
7. **Variability by default** (v0.2+): `seed_strategy: per_run`, каждый
   прогон детерминированно-случайный.

## Definition of Done для каждой фазы

- [ ] Все модули фазы реализованы по сигнатурам из спеки.
- [ ] Все тесты в Tests-секции зелёные.
- [ ] CI зелёный (ruff + pytest на ubuntu и macos).
- [ ] Acceptance-команды работают, выход соответствует описанию.
- [ ] README/docs обновлены если фаза меняет CLI.

## Roadmap дальше (v0.3+ — не план, идеи)

- HDR → SDR tonemap.
- Параллельный GPU encoding с partition VRAM.
- Image-based subtitles OCR + re-render.
- Multi-language audio re-balance.
- Distributed batch по нескольким машинам.
- HDR-aware VMAF metric.

См. [мастер-план v0.2 §«После v0.2»](./v0.2-plan.md).
