# yt-uniquifier — отчёт о ручном тестировании

**Дата**: 2026-05-30
**Стенд**: macOS Darwin 25.5.0, ffmpeg 7.1.1 (с libvmaf + videotoolbox), без fpcalc
**Исходник**: `tests/fixtures/720.mp4` (22:24, 444 MB, h264/aac, 1280×720)
**Тестовые клипы** (`tests/fixtures/.gen/`):
- `clip_a.mp4` (30.18 s) — основной
- `clip_b.mp4` (30.18 s) — для `--b-video` blend_b
- `clip_long.mp4` (90.04 s) — для multi-segment / resume

Артефакты прогона: `out/runs/<profile>/` (mp4 + qa.json + qa.html), `out/logs/phase*.log`, `out/phase3/`, `out/phase4/`.

---

## TL;DR

| Категория | Статус |
|---|---|
| CLI surface (version/probe/preflight/run/qa/batch/calibrate/queue/worker/corpus) | работает |
| Multi-segment + resume | OK — корректно |
| GUI bring-up (импорт + инстанциация всех 10 экранов + MainWindow) | OK |
| Single-segment корректность (длительность) | **БАГ** — output на 2.1 с длиннее |
| Дифференциация интенсивности профилей (`soft` → `aggressive`) | **БАГ** — все три дают одинаковый CID 0.969 |
| VideoToolbox-энкодер на macOS | **БАГ** — все профили падают из коробки |
| `legacy_ab` профиль | **БАГ** — выход CID-идентичен входу (1.000) |

**Главный вывод**: пользователь прав — функционал имеет несколько критических дефектов. На macOS без `--encoder libx264` ничего не работает; даже с x264, профили не дают заявленной градации и output длительность ломается на коротких источниках.

---

## CRITICAL

### CRIT-1. На macOS h264_videotoolbox получает `-q:v 50`, который ffmpeg отвергает → падают все профили

**Симптом**: при дефолтном выборе энкодера `yt-uniq run … --profile <любой>` на mac валится с
```
Error: -q:v qscale not available for encoder. Use -b:v bitrate instead.
Conversion failed!
```

**Воспроизведение**:
```bash
yt-uniq run tests/fixtures/.gen/clip_a.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out /tmp/x.mp4 --workers 1 --no-progress --no-qa
```

**Локализация**: `src/yt_uniquifier/core/pipeline.py:432-433` и `:782-783`
```python
if enc.vendor == "videotoolbox":
    return ["-c:v", name, "-q:v", "50"]
```
Этот ffmpeg-билд (`enable-videotoolbox`) поддерживает только `-b:v`/`-allow_sw`. Текущий код просто не работает.

**Workaround**: `--encoder libx264` форсирует x264.

**Fix-направление**:
- Переключить videotoolbox на `-b:v <max_bitrate> -realtime 0` (как nvenc), либо
- Добавить `-allow_sw 1 -q:v 50` (требует тест, что билд поддерживает constant-quality), либо
- Детектить поддержку `-q:v` в `pick_encoder` через probe и фолбэк.

**Также**: тот же дефект для `hevc_videotoolbox` (строки 432, 782 — единая ветка).

---

### CRIT-2. Single-segment путь даёт output на 2.1 с длиннее входа

**Симптом** (7 из 8 профилей на 30-секундном клипе):

| profile | input dur | output dur | dur_match |
|---|---|---|---|
| soft | 30.183 s | **32.280 s** | false |
| medium | 30.183 s | **32.280 s** | false |
| aggressive | 30.183 s | **32.280 s** | false |
| cid_aware | 30.183 s | **32.280 s** | false |
| cid_aggressive | 30.183 s | **32.604 s** | false |
| medium_hdr | 30.183 s | **32.280 s** | false |
| cid_aware_hdr_to_sdr | 30.183 s | **32.280 s** | false |
| **legacy_ab** | 30.183 s | **30.000 s** | true (другой пайплайн) |

Важно: на multi-segment (`clip_long.mp4` 90.04 с с `--segment-sec 30`) баг **не воспроизводится** (90.047 vs 90.040 — Δ 7 мс). То есть проблема локализована в single-segment fast path.

**Корневая причина** (`src/yt_uniquifier/core/segmenter.py:199-217` — `stream_copy_extract`):
- Source mp4 имеет `container.duration = 30.183`, но реальные packet-таймстампы тянутся до ~32.44 с (edit list / нечётко обрезанный mp4).
- `ffmpeg -ss 0 -to 30.183 -i src -c copy -avoid_negative_ts make_zero seg.mkv` копирует все пакеты до конечного PTS, mkv-контейнер при этом фиксирует реальную длительность (32.44 с), а не нашу `-to`.
- В multi-segment кейс `-to` приходится на серединные keyframe-границы, которые попадают точно в пакетные границы → длительность совпадает.

**Fix-направления** (выбрать одно):
1. После `stream_copy_extract` запускать `ffmpeg -i seg.mkv -t (end-start) -c copy seg_trim.mkv`, либо
2. Поменять `-to` на `-t (end-start)` (явное ограничение длины), либо
3. Для single-segment path **пропустить** `stream_copy_extract` и кормить filter_complex напрямую из источника с `-ss/-t`.

---

### CRIT-3. Профили `soft` / `medium` / `aggressive` дают идентичный результат по CID-метрике

Замер `cid_predict_self` (предсказанная вероятность само-матча Content ID) на одном и том же `clip_a.mp4`:

| profile | cid_predict_self | phash_sim | ssim |
|---|---|---|---|
| soft | **0.969** | 0.804 | 0.766 |
| medium | **0.969** | 0.803 | 0.758 |
| aggressive | **0.969** | 0.808 | 0.757 |
| cid_aware | 0.938 | 0.761 | 0.714 |
| cid_aggressive | **0.844** | 0.696 | 0.681 |
| medium_hdr | 0.969 | 0.808 | 0.773 |
| cid_aware_hdr_to_sdr | 0.938 | 0.780 | 0.774 |
| legacy_ab | **1.000** | 0.708 | 0.794 |

**Проблема**: пользователь выбирает между soft / medium / aggressive в расчёте на градацию интенсивности — но по основной метрике (CID) все три **равны**. Метрики SSIM/pHash тоже почти не отличаются (Δ ≈ 0.01).

**Гипотеза** (требует проверки): YAML-параметры `intensity` в этих профилях не доходят до фактических ffmpeg-фильтров, или клемпятся одинаково. Альтернатива — алгоритм CID-предсказания нечувствителен к этому диапазону трансформ.

**Следующий шаг для отдельного /plan**: сравнить итоговую `-filter_complex` строку для soft vs aggressive — если она отличается слабо, баг в `profile_loader` → `TransformConfig` mapping. Если сильно — баг в `cid_predict`.

---

### CRIT-4. Профиль `legacy_ab` даёт `cid_predict_self = 1.0`

Идеальный само-матч = выход неотличим от входа по chunk-similarity. То есть профиль **полностью не выполняет свою функцию** для основной задачи (uniquification).

`legacy_ab` использует другой пайплайн (`scale2ref is deprecated, use scale=rw:rh instead`) и сохраняет длительность 30.000 — это легаси-прототип, и его поведение явно деградировало. Следует либо удалить из числа shipped-профилей, либо починить.

---

## HIGH

### HIGH-1. Calibrate: VMAF возвращает 0.00 на коротких клипах, пайплайн молча падает на SSIM

```
factor 1.00 → self_match 0.9688, quality 3.8 (vmaf)
factor 1.50 → self_match 0.9375, quality 78.1 (ssim) (VMAF returned 0.00 (unreliable on this pair); falling back…)
```

Quality 3.8 на первой итерации означает, что калибровщик считает выход «ужасного качества» и не пытается повысить интенсивность; вторая итерация падает на SSIM и квалити подскакивает до 78.1. Это нестабильный сигнал, который сводит сходимость к нулю.

**Локализация**: смотри `core/qa/vmaf.py` / `core/calibrate.py`. На 15-секундных клипах VMAF действительно нестабилен; стоит либо повысить минимальную длину для VMAF (например, 30 с), либо сразу SSIM, либо комбинировать честнее.

---

### HIGH-2. `cid_aggressive` берёт 416 с на 30-секундный клип (×14 от реального времени)

CPU x264 single-pass + полный набор трансформ (включая `blend_b` с внешним входом) даёт ratio ~0.07× realtime. На 22-минутном исходнике это будет ≈ 5 ч. Это формально работает, но юзер-экспириенс очень плохой.

Корень — в количестве/параметрах трансформ профиля плюс `--preset slow` для libx264 (`pipeline.py:435`). Возможно стоит:
- Дефолт `preset medium` для x264.
- В профилях с большим числом трансформ замерить, что именно блокирует throughput.

---

### HIGH-3. Раздутие битрейта на `cid_aware` / `cid_aggressive`

| profile | output size (30 с) | bitrate (≈) |
|---|---|---|
| soft | 8.7 MB | 2.4 Mbps |
| medium | 11.6 MB | 3.1 Mbps |
| aggressive | 24.2 MB | 6.6 Mbps |
| cid_aware | **61.2 MB** | **17 Mbps** |
| cid_aggressive | **100.0 MB** | **27 Mbps** |

В 7-12× больше «обычного». Скорее всего CRF 18 (`pipeline.py:435`) + добавленный noise/grain тратит много битов на стохастический шум. Если намерение — сохранить high-quality артефакт от трансформ, это ОК; если нет — стоит зажать `-maxrate` или повысить CRF.

---

## MEDIUM

### MED-1. QA-вердикт «GREEN» при `duration_match: false`

См. `tests/fixtures/results/out_cid_v1.mp4.qa.html`:
- `duration_match: False` (input 30.183, output 32.28)
- При этом «Verdict: GREEN — All metrics within expected bands.»

Если duration не сошёлся — это уже не green. Вердиктовая логика игнорирует duration-match. Смотри `core/qa/report.py::build_report` / `render_html`.

### MED-2. `yt-uniq probe` не поддерживает `--json`

Помогает в скриптинге; `preflight` и `qa` имеют `--json`/`--no-cid-predict` — `probe` нет. Минор UX.

### MED-3. Сильное логирование stderr ffmpeg при падении

Когда videotoolbox падает, оркестратор печатает 30+ строк raw ffmpeg-лога. Стоит ужать до summary + сохранять полный лог в work-dir.

### MED-4. Calibrate `--out` не создаёт parent dir

Если родителя нет — у меня сработало после `mkdir -p`. Стоит автосоздавать.

---

## LOW / NOTES

- `scale2ref is deprecated` в `legacy_ab` — переключить на современный `scale=rw:rh`.
- `fpcalc` отсутствует на стенде; аудио fingerprint корректно gracefully skip'ается, в QA-репортах пишется `audio_fp: fpcalc not in PATH (install chromaprint to enable)` — это **правильное** поведение.
- `qt.qpa.fonts: missing "Sans Serif"` warning на offscreen-тестах — косметика.
- `--fast-qa` есть только на `yt-uniq run`, на `yt-uniq qa` его нет. Стоит унифицировать.

---

## Что работает корректно (regression-baseline)

- `yt-uniq version` / `probe` / `preflight`
- `yt-uniq batch` (2 файла, оба ок)
- `yt-uniq queue init/add/status` + `yt-uniq worker --stop-after-empty`
- `yt-uniq corpus add/list`
- Multi-segment + resume (`segment-sec 30` на 90-секундном клипе): 3 сегмента, resume за 1 с при втором запуске
- GUI: импорт `app_pyqt.main`, все 15 воркеров и 10 экранов импортируются, инстанциируются с `AppState()`, `MainWindow` поднимается под `QT_QPA_PLATFORM=offscreen`
- HDR-профили (`medium_hdr` → libx265 HEVC, `cid_aware_hdr_to_sdr` → libx264) корректно отрабатывают на SDR-источнике с `--encoder libx264`/auto

---

## Приоритеты для следующих /plan'ов

1. **CRIT-1** (videotoolbox) — блокер для mac-юзеров, фикс одной правки в `pipeline.py`.
2. **CRIT-2** (single-segment duration) — корректность выхода, легко воспроизводится.
3. **CRIT-3** (профили не различаются) — основная фича, без неё проект не имеет смысла.
4. **CRIT-4** (`legacy_ab` сломан) — простое решение: удалить из shipped-профилей.
5. **MED-1** (QA-вердикт игнорирует duration) — однострочная правка в `report.py`.
6. **HIGH-1** (calibrate VMAF instability) — выбрать одну метрику и не флапать.
7. **HIGH-2 / HIGH-3** — после остальных, эти про оптимизацию/конфиг.
