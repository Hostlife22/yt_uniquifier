# Spec 09 — Calibration Loop

> **Phase 9 (v0.2)** · 2 дня · **Deps:** [07](./07-audio-strong-variability.md), [08](./08-fingerprint-aware-qa.md)

## Goal

Автоматизированная подстройка интенсивности профиля под целевую
`match_probability` пользователя. Это переводит выбор параметров из режима
«угадай, что включить» в режим «задал target=0.2, получил рабочий профиль».

## Scope

**In:**

- `core/calibration/intensity.py` — `scale_profile(profile, factor)`:
  пропорциональное масштабирование всех transform-параметров.
- `core/calibration/loop.py` — `calibrate(input, base_profile, target, …)`:
  bisect-loop с короткими прогонами + финальная валидация.
- `cli/cmd_calibrate.py` — `yt-uniq calibrate …`.
- `core/profile_loader.py` — `dump_profile(profile, path)`: YAML-serialize.
- Калибровка считается на **первых 60 секундах** исходника, не на полном
  файле — иначе одна итерация = encode целого фильма.

**Not in:** калибровка по нескольким исходникам сразу; калибровка через
реальный YouTube API (нет публичного CID API); ML-based optimizer.

## Modules

### `core/calibration/intensity.py`

```python
from yt_uniquifier.core.models import Profile, TransformConfig

def scale_profile(profile: Profile, factor: float) -> Profile:
    """Return a copy with all transform 'intensities' multiplied by factor.

    factor=1.0 → unchanged. factor=2.0 → 2× более агрессивно. factor=0.5 →
    мягче.

    Per-transform scaling rules:
        video.crop_resize.max_strength  ← *= factor
        video.color_eq.brightness       ← *= factor (around 0)
        video.color_eq.contrast         ← around-1 mul: 1 + (c-1)*factor
        video.color_eq.gamma            ← around-1 mul
        video.color_eq.saturation       ← around-1 mul
        video.noise.strength            ← int(*= factor), clamped [0, 100]
        video.rotate.degrees            ← *= factor
        video.speed.rate                ← around-1 mul
        audio.pitch_tempo.pitch         ← around-1 mul
        audio.pitch_tempo.randomize_within ← *= factor
        audio.eq.bands                  ← gain *= factor (freq unchanged)
        audio.resample.intermediate_sr  ← around-target mul of delta
        audio.spectral_smear.intensity  ← *= factor (clamped [0, 0.1])
    Bounds-checked: never crosses per-transform pydantic limits.
    """


def _around_one_scale(value: float, factor: float) -> float:
    """Scale a value defined as 'around 1.0' (contrast, pitch, gamma)."""
    return 1.0 + (value - 1.0) * factor
```

Каждый transform отдаёт свой `scale_param(name, value, factor)` через
extending `TransformSpec` (новое опциональное поле `scale_fn`). Если spec
не определяет — fallback на `_default_scale_fn` (умножает числовые поля
без around-1 семантики).

### `core/calibration/loop.py`

```python
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.models import Plan, Profile
from yt_uniquifier.core.qa.cid_predict import predict, CIDPredictResult


@dataclass(frozen=True)
class CalibrationTarget:
    max_self_match: float = 0.2
    min_vmaf: float = 88.0
    max_iterations: int = 5
    test_clip_sec: float = 60.0   # cut first N sec for fast calibration


@dataclass(frozen=True)
class CalibrationStep:
    iteration: int
    intensity_factor: float
    profile: Profile
    self_match: float
    vmaf: float | None
    duration_sec: float


@dataclass(frozen=True)
class CalibratedResult:
    profile: Profile
    steps: list[CalibrationStep]
    converged: bool
    final_self_match: float
    note: str | None


def calibrate(
    input_path: Path,
    base_profile: Profile,
    target: CalibrationTarget,
    *,
    work_dir: Path,
    encoder_override: str | None = None,
    on_step: Callable[[CalibrationStep], None] | None = None,
) -> CalibratedResult:
    """
    Bisect intensity_factor in [0.5, 4.0]:
      1. Encode first test_clip_sec via base × current factor.
      2. Predict self-match; measure VMAF.
      3. If self_match > target.max_self_match → factor *= 1.5 (more aggressive).
      4. Else if VMAF < target.min_vmaf → factor /= 1.3 (back off, lighter).
      5. Else → converged.
      6. After max_iterations, return best (lowest self_match with VMAF ok).
    """
```

Реализация:
- Использует `core/segmenter.stream_copy_extract` для вырезания первых
  `test_clip_sec` исходника (быстрая stream-copy).
- Каждая итерация делает полный pipeline-прогон через
  `orchestrator.run_full` на тестовом клипе, затем `predict` + `vmaf.compute`.
- Резолвит конфликт (self_match слишком высок И VMAF просел) → возвращает
  best-so-far с `converged=False, note="quality/uniqueness conflict"`.

### `core/profile_loader.py` — `dump_profile`

```python
import yaml

def dump_profile(profile: Profile, path: Path) -> None:
    """Serialize a Profile to YAML, suitable for re-loading."""
    data = profile.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
```

### `cli/cmd_calibrate.py`

```python
import typer
from pathlib import Path
from rich.console import Console

console = Console()

def calibrate_cmd(
    input: Path = typer.Argument(  # noqa: A002
        ..., exists=True, dir_okay=False, readable=True,
    ),
    base: Path = typer.Option(..., "--base", help="Starting profile YAML."),
    out: Path = typer.Option(..., "--out", help="Where to write the tuned profile."),
    target_match: float = typer.Option(0.2, "--target", help="Max acceptable self-match (0..1)."),
    min_vmaf: float = typer.Option(88.0, "--min-vmaf"),
    max_iterations: int = typer.Option(5, "--iterations"),
    test_clip_sec: float = typer.Option(60.0, "--clip-sec"),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    work_dir: Path = typer.Option(Path(".yt_uniq_calib"), "--work-dir"),
) -> None:
    """Iteratively tune the profile intensity until predicted self-match drops below target."""
```

Output на stdout — таблица шагов через `rich.table`:
```
iter  factor  self_match  vmaf   note
 1    1.00    0.78        92.4   too similar, scale up
 2    1.50    0.55        91.1   still too similar
 3    2.25    0.31        89.7   close, scale up
 4    3.00    0.18        87.4   ✓ within target
```
Финальный YAML записывается в `out`.

Регистрируется в `app.command("calibrate")(calibrate_cmd)`.

## Acceptance

```bash
# Calibrate a profile for a specific input.
yt-uniq calibrate ~/master.mp4 \
  --base src/yt_uniquifier/profiles/cid_aware.yaml \
  --out ./tuned_master.yaml \
  --target 0.2

# Output:
# Iteration 1/5: factor=1.00, self_match=0.61, vmaf=91.3 — scale up
# Iteration 2/5: factor=1.50, self_match=0.38, vmaf=90.1 — scale up
# Iteration 3/5: factor=2.25, self_match=0.17, vmaf=88.6 — converged ✓
# Wrote: ./tuned_master.yaml

# The tuned profile is a normal profile, usable everywhere.
yt-uniq run ~/master.mp4 --profile ./tuned_master.yaml --out ~/master_uniq.mp4

# Re-verify on the full file.
yt-uniq qa ~/master.mp4 ~/master_uniq.mp4 --vs-corpus
# cid_predict_self ~ 0.18 (matches calibration)
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_intensity_scaling.py` | `scale_profile(medium, 2.0)` → max_strength ×2, contrast moves further from 1.0, gamma further from 1.0 etc.; bounds respected (no overflow past pydantic Field ge/le) |
| Unit | `tests/unit/test_intensity_scaling_around_one.py` | `_around_one_scale(1.02, 2.0) == 1.04` |
| Unit | `tests/unit/test_calibration_loop_mocked.py` | mock predict() возвращает заданную последовательность → loop вышел на нужный factor |
| Unit | `tests/unit/test_calibration_quality_conflict.py` | mock: self_match всегда > target, VMAF просел → returns best-so-far с note |
| Unit | `tests/unit/test_dump_profile_roundtrip.py` | `dump_profile(p, path)` → `load_profile(path)` == `p` |
| Integration | `tests/integration/test_calibrate_real_tiny.py` | calibrate на 2-сек tiny_clip с target=0.6, max_iterations=2 — корректно завершается без exception |

## Risks

| Риск | Митигация |
|---|---|
| Каждая итерация = полный encode → калибровка идёт часами | Калибровка на test_clip_sec (60s по умолчанию) через stream_copy_extract |
| Калибровка на первых 60 сек ≠ репрезентативна для всего фильма | Документировать в `cmd_calibrate --help`: «test clip is a heuristic; full-file QA recommended after» |
| Не сходится за `max_iterations` | Возвращаем best-so-far + `converged=False`; CLI exit code 2 (предупреждение, не fail) |
| Quality просел ниже min_vmaf и self_match всё ещё выше target | Возвращаем best-VMAF-with-lowest-self-match, note=«quality vs uniqueness conflict», пользователь решает |
| `scale_profile` теряет non-numeric поля (rng_seed) | scale_fn explicitly preserves non-scaled fields; covered in roundtrip test |
| Профиль `legacy_ab` (blend_b) — нечего масштабировать | scale_profile no-op для blend_b.opacity (значение 0.03 уже на грани); calibrate не рекомендован для blend_b профилей |

## Hand-off

После Phase 9:
- `yt-uniq calibrate` доступен как production-команда.
- Workflow: пользователь раз калибрует под свой типичный контент → получает
  `tuned.yaml` → batch использует его без повторной калибровки.
- Phase 10 (scale validation) тестирует, что калибровка стабильна
  на действительно длинных файлах.
