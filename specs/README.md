# yt-uniquifier — Specs Index

Эти спеки декомпозируют [мастер-план](/Users/admin/.claude/plans/snoopy-sprouting-kay.md) в исполнимые куски. Каждая фаза — отдельный файл со своим **Goal / Scope / Modules / Acceptance / Tests / Deps**.

## Как читать

- **Goal** — что после фазы будет работать end-to-end.
- **Scope** — что входит и явный «not in scope».
- **Modules** — список файлов с публичными сигнатурами и ответственностью.
- **Acceptance** — наблюдаемые критерии готовности (команды, выходы, метрики).
- **Tests** — обязательные тесты по уровням (unit / integration / smoke).
- **Deps** — какие спеки должны быть завершены до старта этой.

## Граф зависимостей

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

## Порядок реализации

| # | Файл | Дни | Можно параллелить |
|---|------|-----|-------------------|
| 0 | [00-bootstrap.md](./00-bootstrap.md) | 0.5 | — |
| 1 | [01-probe-encoder-models.md](./01-probe-encoder-models.md) | 1-2 | — |
| 2 | [02-transforms-pipeline-runner.md](./02-transforms-pipeline-runner.md) | 3-4 | — |
| 3 | [03-segmenter-resume-metadata-preflight.md](./03-segmenter-resume-metadata-preflight.md) | 2-3 | с #4 |
| 4 | [04-qa-report.md](./04-qa-report.md) | 2 | с #3 |
| 5 | [05-gui-docs.md](./05-gui-docs.md) | 2 | после #3 и #4 |

**Итого v1:** 10-13 дней.

## Принципы

1. **Ядро без UI и без ffmpeg-специфики.** `Plan` (pydantic) — JSON-сериализуемый контракт между слоями.
2. **Один `ffmpeg -filter_complex` per сегмент.** Никакого `bgr24` через Python stdin.
3. **Resume через split-process-concat**, не через keyframe-seek.
4. **GUI — тонкая обёртка.** Бизнес-логика только в `core/`.
5. **Тесты на каждый transform** через snapshot сгенерированной строки `filter_complex`.
6. **Graceful degradation** для опциональных бинарников (`fpcalc`, `libvmaf`).

## Definition of Done для каждой фазы

- [ ] Все модули фазы реализованы по сигнатурам из спеки.
- [ ] Все тесты в Tests-секции зелёные.
- [ ] CI зелёный (ruff + pytest на ubuntu и macos).
- [ ] Acceptance-команды работают, выход соответствует описанию.
- [ ] README/docs обновлены если фаза меняет CLI.

## Что лежит за рамками всех спек (v1)

См. секцию «Вне скоупа v1» в [мастер-плане](/Users/admin/.claude/plans/snoopy-sprouting-kay.md).
