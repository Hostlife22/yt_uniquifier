# Plan: Real-Video Bug Hunt (deep coverage)

**Source**: conversational `/ecc:plan` — выявить баги и проблемы на реальных видео, протестировать различные кейсы, исправить найденное.
**Selected Milestone**: ad-hoc QA sweep against `v0.5.x`
**Complexity**: Large
**Date**: 2026-05-31

## Summary
Построить генерируемый матричный харнесс, прогнать `yt-uniquifier` через 7 профилей × ~10 классов входа × 2 пути энкодера × 3 режима запуска (CLI/batch/queue+resume), триажить найденное по серьёзности и закрыть Critical/High регрессионными интеграционными тестами против синтетических клипов (без бинарных fixtures в git).

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Test generation | `tests/conftest.py::tiny_clip` | Генерация через `ffmpeg lavfi` (`testsrc2` + `sine`) — никаких бинарных fixtures |
| Bench harness shape | `tools/benchmark.py` | CSV-матрица → `core.orchestrator.run_full` → агрегатор в `out/runs/` |
| Aggregation | `out/runs/_analyze.py`, `_analysis.json` | JSON-схема для свода результатов |
| Integration test skip | `tests/conftest.py::needs_ffmpeg` | Маркер `@pytest.mark.integration` + skip-маркер при отсутствии ffmpeg |
| Isolated cache | `tests/conftest.py::isolated_cache` | Любые новые тесты обязаны редиректить `CACHE_PATH` в `tmp_path` |
| Per-segment seed | `core/seed_resolver.py::derive_segment_seed` | Воспроизводимость — builders принимают `rng` kwarg |
| Resume атомарность | commit `9dbc8fa` (race condition fix), `7338fa1` (idempotent resume) | `CheckpointStore` потокобезопасен, `_lock: RLock` + fsync + `os.replace` |

## Provided inputs
`tests/fixtures/.gen/`: `clip_a.mp4`, `clip_b.mp4` (720p25 SDR h264 AAC stereo, 30s), `clip_long.mp4` (720p25 SDR, 90s).
**Покрывают**: одна ячейка (SDR 720p25 h264). **Не покрывают**: HDR, 4K, odd-dim, VFR, 5.1, mono, hot/quiet audio, encoder fallback под сложным входом.
**Решение**: расширенный корпус генерируется через `ffmpeg lavfi` в Phase 1 рядом с предоставленными клипами; `clip_long.mp4` используется для resume-теста.

## Files to Change

| File | Action | Why |
|---|---|---|
| `tools/real_video_matrix.py` | CREATE | Главный харнесс: генерирует корпус, строит матрицу, запускает прогоны, агрегирует результаты |
| `tools/_corpus_gen.py` | CREATE | Чистая функция генерации синтетических входов (выносится отдельно для unit-теста) |
| `out/runs/real_matrix_<ts>/summary.csv` | CREATE (runtime) | Свод по ячейкам |
| `out/runs/real_matrix_<ts>/<cell>/` | CREATE (runtime) | Per-cell артефакты: stderr.log, qa.json, state.json snapshot |
| `docs/bug-triage-2026-05-31.md` | CREATE | Триажный отчёт с findings |
| `tests/integration/test_real_matrix_regression_*.py` | CREATE (per finding) | По одному файлу на класс бага, против сгенерированных клипов |
| `CHANGELOG.md` | UPDATE | Под новым патч-разделом перечислить фиксы |
| `cspell.json` | UPDATE | Добавить `testsrc`, `loglevel`, `ultrafast`, `lavfi`, `loudnorm`, `mandelbrot` |

`core/` файлы будут править только по находкам Phase 5. Не закладываем превентивные правки.

## Tasks

### Task 1 — Подготовить окружение
- **Action**: `brew install chromaprint` (получить `fpcalc`), убедиться `make dev` свеж.
- **Validate**: `which fpcalc && yt-uniq probe` без ошибок.

### Task 2 — `tools/_corpus_gen.py`
- **Action**: Функции `gen_sdr_4k`, `gen_hdr10`, `gen_hlg`, `gen_odd_dim`, `gen_vfr`, `gen_60fps`, `gen_2398fps`, `gen_5_1_audio`, `gen_mono_audio`, `gen_hot_audio`, `gen_quiet_audio`. Каждая — ffmpeg lavfi команда возвращает `Path`, идемпотентна (skip если файл уже есть и хэшируется).
- **Mirror**: `tests/conftest.py::tiny_clip` (lavfi-генерация).
- **Validate**: `pytest tests/unit/test_corpus_gen.py -q` (unit-тест: проверка что генератор возвращает существующий файл с ожидаемым codec/transfer/channels через `ffprobe`).

### Task 3 — `tools/real_video_matrix.py`
- **Action**: CLI (`typer`) с `--inputs-dir`, `--profiles`, `--encoders`, `--workers-list`, `--include-resume`, `--out-dir`. Строит матрицу, последовательно запускает (параллелим только внутри одного прогона через `workers`), пишет per-cell артефакты и `summary.csv`. Resume-тест: запускаем `yt-uniq run` через `subprocess.Popen`, ждём первого `segment_done` в stderr, SIGINT, перезапускаем, проверяем что `state.json.segments_done` сохранилось.
- **Mirror**: `tools/benchmark.py` shape.
- **Validate**: `python -m tools.real_video_matrix --inputs-dir tests/fixtures/.gen --profiles soft,medium --encoders libx264 --workers-list 1 --out-dir out/runs/_dryrun_$(date +%s)` завершается с exit 0 на 30-сек клипах.

### Task 4 — Запустить полную матрицу
- **Action**: 7 профилей × (3 предоставленных + ~8 сгенерированных входов) × (libx264, auto) × (workers=1, workers=4) + 1 resume-cell на `clip_long`.
- **Validate**: `summary.csv` имеет все ячейки, ненулевые exit codes сгруппированы.

### Task 5 — GUI deep sweep
- **Action**: Прогон `scripts/manual_gui_smoke.sh` + ручная проверка всех 10 экранов под реальным окном. Запись скриншотов в `out/runs/real_matrix_<ts>/gui/`.
- **Validate**: Каждый экран открывается без uncaught exceptions (мониторим stderr GUI worker).

### Task 6 — Triage report
- **Action**: Написать `docs/bug-triage-2026-05-31.md` со строкой на каждое findings: severity (Critical/High/Medium/Low), категория (crash/corruption/functional/cosmetic), input class, profile, encoder, repro one-liner, log excerpt, гипотеза причины.
- **Validate**: Markdown lints чисто, все Critical имеют repro.

### Task 7 — Фиксы Critical + High
- **Action**: Per finding — failing regression test против сгенерированного клипа → fix в `core/` → green. Никаких opportunistic-рефакторов. Каждый fix отдельным коммитом со ссылкой на triage-отчёт.
- **Mirror**: TDD из `~/.claude/rules/common/testing.md`.
- **Validate**: `make check` зелёный после каждого фикса.

### Task 8 — Re-run + CHANGELOG
- **Action**: Повторный прогон только провалившихся ячеек. Запись в `CHANGELOG.md` под `## [Unreleased] / ### Fixed`.
- **Validate**: Нули в Critical-колонке `summary.csv`, `make check` зелёный.

## Validation
```bash
# Phase 1 sanity
which fpcalc && yt-uniq probe
python -m tools.real_video_matrix --help

# Phase 2 dry-run на предоставленных клипах
python -m tools.real_video_matrix \
  --inputs-dir tests/fixtures/.gen \
  --profiles soft,medium \
  --encoders libx264 \
  --workers-list 1 \
  --out-dir out/runs/_dryrun

# Full matrix
python -m tools.real_video_matrix \
  --inputs-dir tests/fixtures/.gen \
  --generate-corpus \
  --profiles soft,medium,medium_hdr,aggressive,cid_aware,cid_aware_hdr_to_sdr,cid_aggressive \
  --encoders libx264,auto \
  --workers-list 1,4 \
  --include-resume \
  --out-dir out/runs/real_matrix_$(date +%Y%m%d_%H%M)

# После фиксов
make check
```

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| Полная матрица съест >50GB в `out/runs/` | High | Per-cell артефакты ограничены: только stderr tail (2KB), `qa.json`, и финальный mp4 удаляется после QA (флаг `--keep-outputs` для исключений) |
| HDR10 lavfi-генерация на macOS не даст истинного PQ | Medium | Использовать `colorspace=all=bt2020:trc=smpte2084:fmt=yuv420p10le` после `geq` + явный `-color_*` теги; валидировать через `ffprobe color_transfer=smpte2084` |
| Resume race в `workers=4` не воспроизводится 1:1 | Medium | Запускать resume-cell 3 раза, фиксировать любой случай где `segments_done` уменьшается между прогонами |
| Слишком много findings → выйдем за день | Medium | Per-fix budget 1 час. Если фикс шире — добавляем в backlog, не блокируем re-run |
| VideoToolbox encoder может крашить на synthetic 10-bit | Low | Все cells без `workers` логируют stderr; падение VT ловится как High-severity finding, не блокер |
| Долгий прогон (часы) — потеря контекста сессии | Medium | Матрицу запускать через `tools/real_video_matrix.py` отдельным процессом, прогресс в `summary.csv`. Можно прервать и продолжить |

## Acceptance
- [ ] `tools/_corpus_gen.py` + unit test зелёные
- [ ] `tools/real_video_matrix.py` строит и завершает матрицу с `summary.csv`
- [ ] `docs/bug-triage-2026-05-31.md` создан с per-finding записями
- [ ] Все Critical и High закрыты регрессионными тестами в `tests/integration/`
- [ ] Re-run матрицы: нули в Critical
- [ ] `make check` зелёный
- [ ] `CHANGELOG.md` обновлён под `### Fixed`
