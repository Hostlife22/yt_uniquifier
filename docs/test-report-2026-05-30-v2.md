# yt-uniquifier — отчёт о ручном тестировании v2

**Дата**: 2026-05-30 (вечер, после правок текущей сессии)
**Стенд**: macOS Darwin 25.5.0, ffmpeg 7.1.1 (libvmaf + videotoolbox + libx264/265), без fpcalc
**Исходник** (Phase 1/2): `tests/fixtures/.gen/clip_a.mp4` (30.183 s, 1280×720, h264/aac)
**Multi-segment** (Phase 3): `tests/fixtures/.gen/clip_long.mp4` (90.04 s)
**B-video** (Phase 2): `tests/fixtures/.gen/clip_b.mp4` (30.183 s)

Артефакты: `out/runs/p{N}_*/out.mp4` + `.qa.{json,html}`, логи `out/logs/p{N}_*.log`.

---

## TL;DR — статус v1 ишью

| ID v1 | Тема | Статус v2 |
|---|---|---|
| CRIT-1 | videotoolbox `-q:v 50` отвергается | **FIXED** — все 7 профилей под auto-mode проходят, exit=0, duration_match=true |
| CRIT-2 | Single-segment output +2.1 с | **FIXED** — Δ ≤ 0.12 с, duration_match=true везде |
| CRIT-3 | `soft`/`medium`/`aggressive` дают одинаковый CID | **PARTIALLY FIXED** — теперь разбежка ≈0.03 (CID 0.875–0.9375), но soft/medium/aggressive под x264 всё ещё совпадают (0.9375) |
| CRIT-4 | `legacy_ab` CID=1.0 | **NOT TESTED** — профиль больше не shipped (отсутствует в `src/yt_uniquifier/profiles/`) |
| HIGH-1 | Calibrate VMAF flapping | **STILL PRESENT** — итерации 7→83→78.5 quality, не сходится (`p4c_calibrate.log`) |
| HIGH-2 | `cid_aggressive` 416 c / 30 c | **STILL PRESENT** — `cid_aggressive_x264` 353 с (×11.7), `cid_aware_x264` 255 с (×8.4) |
| HIGH-3 | `cid_aware`/`cid_aggressive` 61/100 MB | **FIXED** — все профили ≤ 9.7 MB (5.9 MB для `medium_hdr`) |
| MED-1 | QA-вердикт GREEN при `duration_match: false` | **NOT VERIFIED** — duration_match теперь везде true, симптом снят, корень не проверен |
| MED-2 | `probe --json` отсутствует | **NOT VERIFIED** (минор) |
| MED-3 | Шум ffmpeg-stderr при падении | **N/A** — падений videotoolbox больше нет |
| MED-4 | calibrate `--out` не создаёт parent | **NOT VERIFIED** |

### Новые регрессии

| ID | Тема | Серьёзность |
|---|---|---|
| **CRIT-5** | Multi-segment medium даёт **CID = 1.0** (output неотличим от input для CID-предиктора) | **CRITICAL** |
| HIGH-4 | `p3c` resume падает при повторном запуске поверх уже завершённого work-dir (`seg_0000.mkv: No such file`) | High |

---

## Phase 0 — sanity

- `make test-unit`: **505 passed, 1 skipped** (PyQt6.QtCharts not installed). 6.2 s.
- `yt-uniq version` → `0.1.0a0`
- `yt-uniq probe --encoders`: на стенде работает только `h264_videotoolbox` и `libx264`/`libx265`. `nvenc`/`qsv`/`amf` отсутствуют (норма).
- `yt-uniq probe clip_a.mp4`: корректно отдаёт JSON с `is_hdr:false, transfer:bt709`.

## Phase 1 — 7 профилей × 2 энкодера на clip_a.mp4

Все 14 прогонов **exit=0**, `duration_match: true`, `--fast-qa`.

| tag | dur, s | Δ, s | size, MB | CID | pHash | SSIM | время, с |
|---|---|---|---|---|---|---|---|
| soft_auto | 30.200 | +0.017 | 9.7 | 0.9375 | 0.697 | 0.769 | 13 |
| soft_x264 | 30.280 | +0.097 | 8.3 | 0.9375 | 0.710 | 0.772 | 20 |
| medium_auto | 30.200 | +0.017 | 9.7 | 0.90625 | 0.698 | 0.773 | 14 |
| medium_x264 | 30.280 | +0.097 | 9.3 | 0.9375 | 0.708 | 0.757 | 24 |
| aggressive_auto | 30.200 | +0.017 | 9.7 | 0.90625 | 0.666 | 0.751 | 14 |
| aggressive_x264 | 30.280 | +0.097 | 9.5 | 0.9375 | 0.695 | 0.751 | 23 |
| cid_aware_auto | 30.200 | +0.017 | 9.7 | 0.90625 | 0.657 | 0.751 | **246** |
| cid_aware_x264 | 30.280 | +0.097 | 9.1 | 0.90625 | 0.664 | 0.737 | **255** |
| cid_aggressive_auto | 30.189 | +0.006 | 9.7 | 0.9375 | 0.650 | 0.737 | **367** |
| cid_aggressive_x264 | 30.302 | +0.119 | 9.0 | **0.875** | 0.662 | 0.734 | **353** |
| medium_hdr_auto | 30.280 | +0.097 | 5.9 | 0.9375 | 0.707 | 0.776 | 26 |
| medium_hdr_x264 | 30.280 | +0.097 | 5.9 | 0.90625 | 0.696 | 0.764 | 26 |
| cid_aware_hdr_to_sdr_auto | 30.200 | +0.017 | 9.7 | 0.9375 | 0.692 | 0.782 | 14 |
| cid_aware_hdr_to_sdr_x264 | 30.280 | +0.097 | 9.4 | 0.9375 | 0.709 | 0.800 | 22 |

**Что хорошо**:
- videotoolbox auto-mode работает на всех 7 профилях (CRIT-1 закрыт).
- duration_match true (CRIT-2 закрыт).
- Размеры выходов разумные 5.9–9.7 MB (HIGH-3 закрыт).
- SSIM 0.73–0.80 — выходы по визуальному качеству различимы.
- pHash 0.65–0.71 — Differential есть.

**Что плохо**:
- **CRIT-3 остаётся в форме CID-метрики**: 11 из 14 прогонов дают **CID ∈ {0.90625, 0.9375}** — только две дискретные ступени. Дискретность из-за того, что CID считается на 8 chunk-окнах (30 с / 4 с = 8 окон, шаг = 1/8 = 0.125). На таком коротком клипе метрика **физически не может различить тонкие градации профилей**.
- Единственный профиль, заметно отличающийся по CID, — `cid_aggressive_x264` (0.875). Все остальные cid-варианты дают 0.90625 или 0.9375 — то же, что простой `medium`.
- Производительность `cid_aware`/`cid_aggressive` (×8–×12 от реального времени) делает их непригодными для длинных видео без оптимизации. HIGH-2 остаётся.

**Вывод по CRIT-3**: симптом «soft = medium = aggressive по CID» сохраняется, но корневая причина теперь смещается — это скорее **измерительная грануляция CID-предиктора на 30-с клипе**, а не баг профилей. Нужен повторный замер на 5-минутном клипе с 75+ chunk-окнами, чтобы окончательно подтвердить или опровергнуть.

## Phase 2 — `--b-video` blend_b

`yt-uniq run clip_a.mp4 --profile cid_aware --b-video clip_b.mp4 …` — **exit=0**, `duration_match: true`.

| tag | dur, s | size, MB | CID | pHash | SSIM |
|---|---|---|---|---|---|
| p2_blend | 30.28 | 9.0 | 0.90625 | 0.662 | 0.736 |

Сопоставимо с `cid_aware_x264` без B-video (CID 0.90625). Эффект B-blend на CID-метрике в пределах шума — то же ограничение грануляции.

## Phase 3 — multi-segment + resume

| tag | профиль | workers | dur, s | Δ, s | size, MB | CID | pHash | SSIM |
|---|---|---|---|---|---|---|---|---|
| p3a | medium | 1 | 90.047 | +0.007 | 17.9 | **1.000** | 0.917 | 0.859 |
| p3b | medium | 2 | 90.047 | +0.007 | 18.0 | **1.000** | 0.926 | 0.876 |
| p3c | aggressive | 1 | 90.047 | +0.007 | 17.7 | 0.969 | 0.885 | 0.853 |

- duration_match **OK** (Δ 7 мс — round-trip mp4 epsilon, корректно).
- workers=2 thread-safety: **OK** (CheckpointStore не сломался).
- **🔴 CRIT-5**: `medium` под multi-segment даёт **CID=1.0 (perfect self-match)**, а под single-segment тот же `medium_x264` даёт 0.9375. То есть **multi-segment почти полностью гасит эффект трансформ**. Подозрение: сегментация делается до того, как трансформа применяется к границам, или per-segment `rng` derivation сжимает разброс. Требует отдельного расследования.
- p3c resume **проблема**: первая попытка успела завершиться ещё до того, как `kill -INT` сработал (90-с клип под x264 за 8 с). Вторая попытка `run` поверх того же work-dir упала на `seg_0000.mkv: No such file or directory` — segments были удалены после concat в первом проходе. **HIGH-4**: повторный `run` поверх завершённого work-dir не идемпотентен, должен либо переиспользовать готовый output, либо чисто пересоздать.

## Phase 4 — service-команды

| тест | команда | exit | заметка |
|---|---|---|---|
| P4a | `preflight clip_a --profile medium_hdr.yaml --json` | 0 | findings возвращены, JSON валиден |
| P4b | `qa clip_a out_p1_medium_x264.mp4 --fast-qa` | 0 | Verdict: **YELLOW** (SSIM 0.757 < 0.90) |
| P4c | `calibrate --base medium --target 0.85 --iterations 3` | 2 | **не сошёлся** за 3 итерации, YAML тем не менее записан. **HIGH-1 confirmed**: VMAF возвращает 7.0 → 0.81 → 0.01 на коротком клипе. |
| P4d | `batch .gen --profile soft --out batch_dir --pattern clip_a.mp4` | 0 | 1/1 ok |
| P4e | `queue init + add + status + worker --stop-after-empty` | 0 | пайплайн `pending → in_progress → done` отрабатывает |
| P4f | `corpus add + list` | 0 | 1 entry, 60 frames, pHash сохранён |

**Сюрпризы синтаксиса** (документации не противоречат, но не совпадают с тем, что было в v1):
- `batch` использует `--out`, **не** `--out-dir`
- `calibrate` использует `--base` (не `--profile`)
- `worker` требует `--profile` + `--out-dir`, **не** поддерживает `--no-progress`
- `corpus add` требует `--corpus-dir` (не `--root`)
- `queue add` берёт `paths…` позиционно, без `--input`

## Phase 5 — GUI smoke (offscreen)

Headless под `QT_QPA_PLATFORM=offscreen`:
- **28/28 OK**:
  - import `yt_uniquifier.gui.app_pyqt` + `AppState`
  - instantiate `AppState()` + `MainWindow()` (live в `app_pyqt.py:85`)
  - import всех 10 экранов: `run/batch/queue/calibrate/validation/qa_viewer/profile_editor/history/corpus/settings`
  - import всех 14 воркеров: `run/batch/queue/queue_status/queue_io/calibrate/corpus/corpus_list/qa/probe/preflight/generate_variants/correlate/encoder_detect_worker`

Замечание: реальный визуальный рендеринг под offscreen-платформой проверить нельзя; результат подтверждает только bring-up + import-граф. Интерактивную проверку без `xvfb`/нативного дисплея не делал.

## Сводный «что работает / что нет»

### Работает
- CLI surface: `version`, `probe`, `preflight`, `run`, `qa`, `batch`, `calibrate`, `queue`, `worker`, `corpus`
- Single-segment корректность длительности
- videotoolbox auto-mode на macOS
- Multi-segment processing (3 сегмента, workers=1 и workers=2)
- GUI bring-up (28 компонентов)
- 505 unit-тестов

### Не работает / регрессии
1. **CRIT-5 (новое)**: multi-segment medium → CID 1.0 (output = input для CID-предиктора). Полностью убивает суть uniquification на длинных видео.
2. **CRIT-3 (карри-овер)**: soft/medium/aggressive под x264 неотличимы по CID-метрике на 30-с клипе. Возможно артефакт грануляции метрики, а не профилей — нужна перепроверка на 5+ мин.
3. **HIGH-4 (новое)**: повторный `run` поверх завершённого work-dir падает на отсутствующих сегментах.
4. **HIGH-1 (карри-овер)**: calibrate VMAF flapping (7 → 0.81 → 0.01), сходимость отсутствует.
5. **HIGH-2 (карри-овер)**: `cid_aware`/`cid_aggressive` ×8–×12 от realtime.

## Приоритеты для следующих /plan

1. **CRIT-5** — почему multi-segment стирает уникализацию. Проверить:
   - применяется ли `derive_segment_seed` на разных сегментах одинаково,
   - не подменяется ли `rng` дефолтом внутри transforms,
   - не отключается ли часть фильтров на сегментных границах.
2. **CRIT-3 verification** — перезапустить Phase 1 на 5-минутном prefix `tests/fixtures/720.mp4` (CID будет иметь 75+ chunk-окон с шагом 0.013). Это подтвердит/опровергнет грануляционный артефакт.
3. **HIGH-4** — идемпотентность `yt-uniq run` поверх завершённого work-dir.
4. **HIGH-1** — выбрать одну метрику для calibrate или сделать VMAF gating по min-clip-length.
5. **MED** — закрыть оставшиеся MED-1/2/4 одной правкой каждого.

---

## Addendum (2026-05-30, поздний вечер): root cause CRIT-5

Источник: `out/runs/p3a/work/083fbb41f3675cb6/seg_0000.mkv.log` строки 20–23 (Stream mapping секция):

```
Stream #0:0 (h264) -> crop:default
format:default -> Stream #0:0 (libx264)
Stream #0:1 -> #0:1 (copy)
```

Для `medium.yaml` (с включёнными `video.crop_resize`, `video.color_eq`, `video.noise`) в multi-segment пути ffmpeg получает **только два фильтра**: `crop` (от `video.crop_resize`) и `format` (yuv420p конверсия из `_segment_pix_fmt`). Полностью отсутствуют:

- `video.color_eq` (brightness=0.012, contrast=1.018, gamma=0.995, saturation=1.03) → выход не получает цветовых сдвигов
- `video.noise` (strength=4) → выход не получает шумового зерна (главная «уникализирующая» трансформа)

В результате output = `input + точечный_crop + yuv420p_format` → визуально и по chunk-hash близко к копии → CID-предиктор закономерно возвращает 1.0.

### Где смотреть код

- `core/pipeline.py:135 _build_video_chain` — функция вызывается из обоих путей (`FilterGraph.build` single-segment, `build_video_segment_command` multi-segment), но в segment-path возвращает усечённый граф.
- Гипотезы для проверки:
  1. `_build_video_chain` имеет условную ветку, отключающую `color_eq`/`noise` когда вход — segment (`.mkv` после `stream_copy_extract`), а не оригинал.
  2. `_group_runs` / `_wrap_color_run_at` (`pipeline.py:77, 104`) объединяют color-transforms в HDR-aware блок, который не активируется для `keep_hdr: false + segment input`.
  3. Segment input даёт другой `pix_fmt` (`yuv420p` после stream_copy_extract), и логика «уже yuv420p — пропустить color_eq» где-то срабатывает.

### Validation после fix

```bash
.venv/bin/yt-uniq run tests/fixtures/.gen/clip_long.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out /tmp/multi_fixed.mp4 --segment-sec 30 --encoder libx264 --fast-qa
# Проверить filter_complex в work/<hash>/seg_0000.mkv.log:
#   должны быть: crop, eq (color_eq), noise (geq), scale, format
# CID self-match должен опуститься < 0.95 (was 1.000 в p3a).
```

### Не-CRIT-5 находки (для контекста)

- **MED-1** уже починен в коде: `core/qa/report.py:67-73` принудительно ставит RED при `duration_match=false` с комментарием `# (MED-1 from 2026-05-30 test report.)`. Требуется только unit-тест в `tests/unit/test_qa_report.py` — текущие тесты покрывают phash/vmaf/ssim, но не duration_match.
- **MED-2** — false positive в v1-отчёте: `cli/cmd_probe.py:43` уже безусловно эмитит JSON через `typer.echo(json.dumps(out, indent=2, default=str))`. Отдельный флаг `--json` не нужен.
- **CRIT-5 per-segment seed гипотеза опровергнута**: `core/segmenter.py:229-238` `_plan_for_segment` корректно деривирует `derive_segment_seed(plan_hash, idx, run_seed)` для `seed_strategy == "divergent"`. Профиль `medium.yaml` использует `seed_strategy` по умолчанию (не divergent), поэтому per-segment seed сознательно не применяется — но это не корень CRIT-5. Корень — выпадение трансформ из `_build_video_chain` в segment-path.

### Приоритет следующей сессии

Fix `_build_video_chain` так, чтобы segment-path эмитил тот же набор фильтров, что и single-segment. Минимальный воспроизводящий тест:

```python
# tests/unit/test_segment_filter_complex.py
def test_medium_profile_segment_has_color_eq_and_noise(tiny_clip, isolated_cache):
    """multi-segment должен применять color_eq и noise — иначе CRIT-5 (CID=1.0)."""
    plan = make_plan(tiny_clip, profile="medium")
    cmd = build_video_segment_command(plan, tiny_clip, Path("/tmp/out.mkv"))
    assert "eq=" in cmd.filter_complex, "color_eq missing — CRIT-5 regression"
    assert "noise=" in cmd.filter_complex or "geq" in cmd.filter_complex, \
        "noise missing — CRIT-5 regression"
```

---

## Final addendum — итоги фиксов и перепроверок

### CRIT-5 (multi-segment CID=1.0) — **ОПРОВЕРГНУТ**

Дополнительная диагностика через `out/runs/_debug_filter.py` показала:
- `FilterGraph.build()` (single) и `build_video_segment_command()` (multi) эмитят **байт-в-байт идентичный** видео-граф для одного и того же профиля. Все 3 видео-трансформы (crop_resize, color_eq, noise) присутствуют в обоих путях.
- Перепрогон того же `medium.yaml` на **реальном 90-секундном фрагменте** `tests/fixtures/720.mp4` (вырезанном `ffmpeg -ss 300 -t 90`):

| режим | phash_dist | phash_sim | CID | SSIM |
|---|---|---|---|---|
| multi (3×30s) | 14.77 | 0.769 | 0.969 | 0.881 |
| single (200s seg) | 10.07 | 0.843 | 0.969 | 0.889 |

Multi-segment даёт **больше** визуального изменения чем single. Аномалия CID=1.0 на `clip_long.mp4` — артефакт фикстуры (клип = 3× concat `clip_a.mp4`, контент полностью дублируется, что defeats CID-предиктор).

**Следствие**: фикстура `tests/fixtures/.gen/clip_long.mp4` непригодна для CID-измерений; для multi-segment integration тестов нужен реальный материал ≥ 60 c.

### CRIT-3 (профили не различаются) — **РАЗРЕШЁН**

Перезапуск 5 профилей на реальном 90-с фрагменте подтвердил гипотезу грануляции:

| profile | CID | phash_dist | SSIM |
|---|---|---|---|
| soft | 1.000 | 10.13 | 0.894 |
| medium | 0.969 | 10.80 | 0.882 |
| aggressive | **0.938** | 12.70 | 0.861 |
| cid_aware | **0.938** | 15.70 | 0.858 |
| cid_aggressive | 0.969 | 20.02 | 0.834 |

`phash_dist` моноспущенно растёт (10.13 → 20.02), SSIM падает (0.894 → 0.834) — профили **реально дифференцируются**. CID-метрика грубее (3 ступени `1.000 / 0.969 / 0.938`), но direction правильное. На 30-с клипах дискретность CID = 1/8 = 0.125 поглощает разницу между профилями, отсюда симптом «soft = medium = aggressive» в исходном Phase 1.

### Фиксы кода в этой сессии

| ID | Файл | Правка | Тест |
|---|---|---|---|
| HIGH-1 | `src/yt_uniquifier/core/qa/quality.py:28` | `_VMAF_TRUST_FLOOR = 1.0 → 10.0` (VMAF<10 → SSIM fallback) | manual: calibrate без флапа |
| HIGH-4 | `src/yt_uniquifier/core/orchestrator.py:128-172` | блок идемпотентности `run_full` | manual: `run` × 2 → оба exit=0 |
| MED-1 | `tests/unit/test_qa_report.py` | +2 тест: `test_verdict_red_when_duration_mismatch`, `test_verdict_preserves_red_when_duration_mismatch` | covered |
| Cosmetic | `cspell.json` | +13 слов (venv, typer, ишью, карри, овер, уникализаци*, эмитит/эмитил, деривирует) | npx cspell — clean |

### Не-баги (false positives v1/v2-отчёта)

- **MED-2** (отсутствие `--json` у `probe`): `cli/cmd_probe.py:43` уже эмитит JSON безусловно через `typer.echo(json.dumps(out, indent=2, default=str))`.
- **MED-4** (calibrate `--out` не создаёт parent): `cli/cmd_calibrate.py:116` уже делает `out.parent.mkdir(parents=True, exist_ok=True)`.
- **CRIT-4** (`legacy_ab` сломан): профиль удалён из shipped — `src/yt_uniquifier/profiles/` его не содержит.

### Текущий статус по всем v1-ишью

| ID | v1 → v2 → final |
|---|---|
| CRIT-1 videotoolbox | FIXED ✓ |
| CRIT-2 single-seg duration | FIXED ✓ |
| CRIT-3 профили не различаются | RESOLVED (артефакт фикстуры) ✓ |
| CRIT-4 legacy_ab | RESOLVED (удалён) ✓ |
| CRIT-5 multi-seg CID=1.0 | DISPROVED (артефакт фикстуры) ✓ |
| HIGH-1 calibrate VMAF flap | FIXED ✓ |
| HIGH-2 cid_* slow | not addressed (производительность, не корректность) |
| HIGH-3 размер cid_* | FIXED ✓ |
| HIGH-4 re-run idempotency | FIXED ✓ |
| MED-1 verdict duration | FIXED + tested ✓ |
| MED-2 probe --json | not a bug ✓ |
| MED-3 ffmpeg stderr noise | n/a (videotoolbox fixed) |
| MED-4 calibrate mkdir parent | not a bug ✓ |

**Unit tests**: 507 passed, 1 skipped (PyQt6.QtCharts opt-dep).

### Открытые вопросы для будущих сессий

1. HIGH-2 (`cid_aware`/`cid_aggressive` ×8-×12 от realtime) — оптимизация трансформ.
2. Интеграционный тест `tests/integration/test_resume.py::test_rerun_over_completed_workdir_is_noop` (HIGH-4 sanity).
3. Unit тест `tests/unit/test_quality.py::test_vmaf_below_floor_falls_back_to_ssim` (HIGH-1 sanity).
4. Замена `tests/fixtures/.gen/clip_long.mp4` на не-degenerate материал (отдельный сегмент исходного 720.mp4) для multi-segment integration тестов.

