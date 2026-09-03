# Spec 04 — QA Report

> **Phase 4** · 2 дня · **Deps:** [02-transforms-pipeline-runner](./02-transforms-pipeline-runner.md) · **Parallel:** [03-segmenter](./03-segmenter-resume-metadata-preflight.md)

## Goal

После `yt-uniq run` рядом с выходным файлом появляются `output.qa.json` и `output.qa.html` с метриками similarity input↔output: pHash (visual fingerprint), audio fingerprint (chromaprint), VMAF, SSIM, MD5. Это даёт пользователю объективную оценку «насколько уникален результат» и «насколько просело качество».

Также доступна команда `yt-uniq qa <input> <output>` отдельно — для запуска QA на готовой паре без повторного encode.

## Scope

**In:**

- `core/qa/hashes.py` — потоковый MD5.
- `core/qa/phash.py` — выборка кадров + imagehash → distance distribution.
- `core/qa/audio_fp.py` — chromaprint через `fpcalc` (graceful skip если нет).
- `core/qa/vmaf.py` — ffmpeg libvmaf (graceful skip если нет в ffmpeg).
- `core/qa/ssim.py` — ffmpeg ssim фильтр.
- `core/qa/report.py` — агрегация в `QAReport` + рендеринг HTML (jinja2).
- `core/qa/templates/report.html.j2` — шаблон отчёта.
- `cli/cmd_qa.py` — отдельная команда.
- Интеграция в `cli/cmd_run.py`: после успешного concat вызывает QA, кроме случая `--no-qa`.

**Not in:** heatmap по времени (v1.1), сравнение нескольких выходов одного входа (v1.1), отправка отчёта в внешние системы.

## Modules

### `core/qa/hashes.py`

```python
from pathlib import Path
import hashlib

def md5_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    """Streaming MD5; не загружает файл целиком в память."""
```

### `core/qa/phash.py`

```python
from pathlib import Path
from dataclasses import dataclass
import subprocess
from PIL import Image
import imagehash
import io

@dataclass(frozen=True)
class PHashStats:
    samples: int
    distance_min: int
    distance_mean: float
    distance_max: int
    similarity: float        # 1.0 - mean/64

def sample_frames(path: Path, n: int = 120) -> list[Image.Image]:
    """
    Извлекает n кадров равномерно по времени через ffmpeg:
      ffmpeg -i path -vf "select='not(mod(n,K))',scale=256:-1" -fps_mode passthrough
             -frames:v n -f image2pipe -vcodec png pipe:1
    K вычисляется из total_frames / n.
    Возвращает list[PIL.Image].
    """

def compare(input_path: Path, output_path: Path, n: int = 120) -> PHashStats:
    """
    Извлекаем n кадров из обоих, считаем imagehash.phash, попарно distance.
    similarity = 1 - mean_distance / 64 (phash 8x8 → 64 bit).
    """
```

### `core/qa/audio_fp.py`

```python
import shutil, subprocess, json
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class AudioFPResult:
    available: bool
    similarity: float | None     # 0..1, Jaccard на совпадающих 32-bit subfingerprints
    note: str | None             # причина skip, если available=False

FPCALC_AVAILABLE = shutil.which("fpcalc") is not None

def compare(input_path: Path, output_path: Path) -> AudioFPResult:
    """
    Если fpcalc нет — return AudioFPResult(available=False, similarity=None,
                                            note="fpcalc not in PATH (install chromaprint)").
    Иначе:
      fpcalc -json -length 300 <path>  → {"duration":..., "fingerprint":"..."}
    Декодируем base64 в list[int32], считаем Jaccard на множествах sub-fingerprints.
    """
```

### `core/qa/vmaf.py`

```python
import subprocess
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class VMAFResult:
    available: bool
    score: float | None      # 0..100
    note: str | None

def vmaf_available() -> bool:
    """ffmpeg -hide_banner -filters 2>&1 | grep libvmaf"""

def compute(input_path: Path, output_path: Path, threads: int = 4) -> VMAFResult:
    """
    Если libvmaf нет — graceful skip.
    Иначе:
      ffmpeg -i {output} -i {input} -lavfi
        '[0:v][1:v]libvmaf=n_threads={threads}:log_path=/tmp/vmaf.json:log_fmt=json'
        -f null -
    Парсит вывод JSON, возвращает pooled.harmonic_mean (или mean).
    """
```

### `core/qa/ssim.py`

```python
import subprocess
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class SSIMResult:
    score: float    # 0..1, mean
    score_per_channel: dict[str, float]   # {Y, U, V}

def compute(input_path: Path, output_path: Path) -> SSIMResult:
    """
    ffmpeg -i {output} -i {input} -lavfi '[0:v][1:v]ssim=stats_file=-' -f null -
    Парсит финальную строку 'All:X (...)' + per-channel.
    """
```

### `core/qa/report.py`

```python
from pathlib import Path
from yt_uniquifier.core.models import QAReport, Plan

def build_report(
    input_path: Path,
    output_path: Path,
    plan: Plan,
    *,
    samples: int = 120,
    run_vmaf: bool = True,
    run_audio_fp: bool = True,
    progress: Callable[[str, float], None] | None = None,
) -> QAReport: ...

def render_html(report: QAReport, plan: Plan, dest: Path) -> None:
    """Renders core/qa/templates/report.html.j2 to dest."""

def write_json(report: QAReport, dest: Path) -> None: ...
```

`build_report` собирает:

| Метрика | Источник | Время на 2-час 1080p |
|---|---|---|
| md5 input/output | hashes.md5_file | 30-60 сек (~5GB) |
| phash | phash.compare(n=120) | 20-40 сек |
| audio_fp | audio_fp.compare | 5-10 сек (если fpcalc есть) |
| vmaf | vmaf.compute(threads=N) | **5-15 минут** ← опционально |
| ssim | ssim.compute | 2-5 минут |
| duration_match | abs(out.duration - in.duration) < 0.5s | мгновенно |

Опции `run_vmaf=False`, `run_ssim=False` дают «лёгкий» отчёт (под минуту).

### `core/qa/templates/report.html.j2`

Один-страничный HTML, инлайн CSS, без внешних зависимостей. Секции:

1. **Header**: input/output пути, размеры, длительности, container/codec.
2. **Correctness-first assessment**: `INVALID` для media-contract/decode failure;
   quality (`PASS/WARNING/FAIL/UNAVAILABLE`) и visual similarity
   (`LOW/MODERATE/HIGH/UNAVAILABLE`) показываются независимо.
3. **Metrics table**: все числа с tooltip-объяснениями.
4. **Plan section**: какие transforms применены, с параметрами.
5. **Notes**: warnings (например «libvmaf недоступен, метрика пропущена»).

### `cli/cmd_qa.py`

```python
@app.command("qa")
def qa_cmd(
    input: Path = typer.Argument(..., exists=True),
    output: Path = typer.Argument(..., exists=True),
    plan: Path | None = typer.Option(None, help="Optional plan.json for richer report"),
    samples: int = typer.Option(120, "--samples"),
    no_vmaf: bool = typer.Option(False, "--no-vmaf"),
    no_audio_fp: bool = typer.Option(False, "--no-audio-fp"),
    no_ssim: bool = typer.Option(False, "--no-ssim"),
    json_out: Path | None = typer.Option(None, "--json"),
    html_out: Path | None = typer.Option(None, "--html"),
) -> None: ...
```

По умолчанию пишет `<output_stem>.qa.json` и `<output_stem>.qa.html` рядом с output.

### Интеграция в `cmd_run.py`

```python
def run_cmd(..., qa: bool = True, fast_qa: bool = False):
    # ... после concat:
    if qa:
        report = build_report(
            input=input, output=output, plan=plan,
            run_vmaf=not fast_qa, samples=60 if fast_qa else 120,
        )
        write_json(report, output.with_suffix(".qa.json"))
        render_html(report, plan, output.with_suffix(".qa.html"))
```

Flag `--fast-qa`: пропускает VMAF (самое долгое), уменьшает samples до 60. Удобно при batch.

## Acceptance

```bash
# 1. QA на готовой паре
yt-uniq qa input.mp4 output.mp4
# Создаёт output.qa.json + output.qa.html рядом с output.mp4.
# stdout: краткая сводка с цветным verdict.

# 2. Run с автоматическим QA
yt-uniq run input.mp4 --profile ...medium.yaml --out /tmp/uniq.mp4
ls /tmp/uniq.*
# uniq.mp4  uniq.qa.json  uniq.qa.html

# 3. Fast QA в batch
yt-uniq run input.mp4 --profile ...soft.yaml --out /tmp/u.mp4 --fast-qa
# qa.json без vmaf.score; завершается на минуту-две быстрее на большом файле.

# 4. Graceful skip
# Среда без fpcalc/libvmaf
yt-uniq qa input.mp4 output.mp4
# qa.json содержит audio_fp_similarity=null, vmaf_mean=null;
# notes: ["audio_fp: fpcalc not in PATH", "vmaf: libvmaf not in this ffmpeg build"]
# exit code 0.

# 5. Открыть HTML
open /tmp/uniq.qa.html
# Видим banner verdict, таблицу метрик, список transforms.
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_qa_hashes.py` | md5 на 10MB бинарном фикстуре, проверка хеша |
| Unit | `tests/unit/test_qa_phash.py` | mock sample_frames возвращает фиксированные PIL.Image, проверка PHashStats математики |
| Unit | `tests/unit/test_qa_phash.py` | идентичные кадры → similarity ≈ 1.0; полностью разные → < 0.6 |
| Unit | `tests/unit/test_qa_audio_fp.py` | mock fpcalc отсутствует → available=False; mock fpcalc + одинаковые JSON → similarity=1.0 |
| Unit | `tests/unit/test_qa_vmaf.py` | mock libvmaf отсутствует → available=False |
| Unit | `tests/unit/test_qa_ssim.py` | парсинг финальной строки ffmpeg ssim |
| Unit | `tests/unit/test_qa_report.py` | build_report с моками всех submodules, проверяет агрегацию + порядок выполнения |
| Unit | `tests/unit/test_qa_report.py` | render_html генерирует валидный HTML, содержит все ключевые поля |
| Unit | `tests/unit/test_qa_verdict.py` | пороги цветного banner: phash 0.99 → red; 0.92 → yellow; 0.80 → green |
| Integration | `tests/integration/test_qa_end_to_end.py` | реальный 2-сек клип, run medium, QA отчёт парсится, phash_similarity < 0.95, ssim > 0.97 |
| Integration | `tests/integration/test_qa_identity.py` | input.mp4 == output.mp4 → phash similarity 1.0, ssim 1.0, vmaf 100 |
| Smoke | `tests/smoke/test_qa_smoke.py` | минимальный QA-вызов без vmaf, exit 0 |

## Risks

- **VMAF медленный.** На 2-час 1080p — 5-15 минут (особенно single-threaded ffmpeg). Решения: `n_threads=auto` параметр, опция `--no-vmaf`, по умолчанию в `cmd_run` включён, в `--fast-qa` режиме выключен.
- **`fpcalc` парсинг JSON.** Зависимость от формата вывода chromaprint. Если в системе очень старая версия — графейся skip + note.
- **`libvmaf` модели.** По умолчанию ffmpeg использует встроенную model_path. Если модель не найдена — warn + skip. Не пытаться скачать.
- **PNG-pipe extraction большого файла** может занять много RAM при больших разрешениях. Решение: `scale=256:-1` ужимает до 256px ширины перед PNG-кодированием.
- **Identity случай** — input == output по содержанию, но contianer-mux разный → md5 разные, но perceptual метрики 1.0. Это корректное поведение — md5 показывает «файл уникален», perceptual — «выглядит и звучит как оригинал».
- **Аудио длительность ≠ видео длительность.** Может быть до ±1 кадра — `duration_match` использует 0.5s tolerance.

## Hand-off в Spec 05

После Phase 4:
- `qa.html` — артефакт, на который GUI делает ссылку «Open report».
- `qa.json` — машинно-читаемый для интеграций (batch-summary, future CI gating).
- Все QA-вызовы изолированы — GUI может вызывать `build_report` напрямую и стримить прогресс через callback.
