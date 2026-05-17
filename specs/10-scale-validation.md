# Spec 10 — Scale Validation

> **Phase 10 (v0.2)** · 1.5 дня · **Deps:** [06](./06-real-hdr-pipeline.md), [07](./07-audio-strong-variability.md), [08](./08-fingerprint-aware-qa.md), [09](./09-calibration-loop.md)

## Goal

Доказать, что pipeline реально работает на полноразмерных файлах (2h+ 1080p,
30 min HDR 4K) — без OOM, без зависаний, без визуальных регрессий, и за
разумное время. Применить найденные на длинных прогонах оптимизации.

## Scope

**In:**

- `tools/benchmark.py` — единственная команда-бенчмарк на реальный фильм.
- `tools/seam_test.py` — verify швов concat'а через SSIM-окно вокруг каждой
  границы сегмента.
- Опции производительности в `core/segmenter.py`: `--workers N` для
  параллельного encoding сегментов на CPU.
- Кеш `list_keyframes` per-input в `~/.cache/yt_uniquifier/keyframes/`.
- Адаптивный `samples` в `core/qa/phash.py`: пропорционально длительности.
- Опция `subsample` в `core/qa/vmaf.py` (`libvmaf=...:subsample=N`).
- CI: добавить unit-тест на planning длинного входа (mock без реального
  encode).

**Not in:** GPU параллелизм (разбиение VRAM, v0.3); distributed batch;
адаптивный re-segmentation при OOM.

## Modules

### `tools/benchmark.py`

```python
#!/usr/bin/env python3
"""Run a single end-to-end benchmark on a real media file."""

import argparse
import csv
import resource
import time
from pathlib import Path

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--encoder", default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--csv", type=Path, default=Path("benchmark.csv"))
    args = ap.parse_args()

    profile = load_profile(args.profile)
    plan = build_plan(args.input, profile, args.encoder)

    metrics: dict[str, float] = {}
    start = time.monotonic()
    phase_times: dict[str, float] = {}

    def on_event(ev):
        # Per-phase wall time tracking
        ...

    options = RunOptions(
        work_dir=Path(".bench_work") / plan.plan_hash,
        output=args.output,
        # workers handled by segmenter internally
    )
    run_full(plan, options, on_event=on_event)

    total = time.monotonic() - start
    rss_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    metrics.update({
        "input": str(args.input),
        "duration_sec": plan.source.duration_sec,
        "size_bytes": plan.source.size_bytes,
        "encoder": plan.encoder.name,
        "workers": args.workers,
        "wall_sec": total,
        "rss_peak_kb": rss_peak_kb,
        **{f"phase_{k}_sec": v for k, v in phase_times.items()},
    })

    _append_csv(args.csv, metrics)
    print(metrics)
```

### `tools/seam_test.py`

```python
#!/usr/bin/env python3
"""Measure SSIM around concat seams. Warn if seams are visually detectable."""

import argparse
import subprocess
from pathlib import Path

from yt_uniquifier.core.checkpoint import CheckpointStore
# … reuse Plan reconstruction from work_dir/state.json …


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path, help="Final mp4 from a yt-uniq run")
    ap.add_argument("--work-dir", required=True, type=Path,
                    help="The .yt_uniq_work/<hash> dir from that run")
    ap.add_argument("--window-frames", type=int, default=2)
    ap.add_argument("--threshold", type=float, default=0.005,
                    help="Acceptable SSIM-delta around the seam")
    args = ap.parse_args()

    # Load state.json -> get segment boundaries in seconds.
    # For each boundary T:
    #   Extract [T-2f, T+2f] from output via ffmpeg.
    #   Compute SSIM in 4 consecutive frames around the seam.
    #   Compare to SSIM in the middle of the same segment (baseline).
    # Report seams where delta > threshold.
```

### `core/segmenter.py` — параллельный encoding

```python
def process_video_segments_parallel(
    pending: list[Segment],
    plan: Plan,
    work_dir: Path,
    workers: int = 1,
    *,
    on_event=None,
    cancel_token=None,
) -> list[tuple[Segment, Path, Path]]:
    """Process N segments in parallel.

    Only safe when encoder is libx264/libx265 (CPU). For GPU encoders,
    silently fall back to workers=1.
    """
    if workers <= 1 or plan.encoder.vendor not in ("x264", "x265"):
        # sequential fallback
        return [process_video_segment(s, plan, work_dir, on_event=on_event,
                                       cancel_token=cancel_token)
                for s in pending]
    # Use concurrent.futures.ProcessPoolExecutor for true parallelism
    # (subprocess.Popen already releases GIL but ProcessPool isolates state).
```

Orchestrator принимает `workers` через `RunOptions.workers: int = 1`.

### `core/segmenter.py` — кеш keyframes

```python
KEYFRAME_CACHE = Path.home() / ".cache" / "yt_uniquifier" / "keyframes"

def list_keyframes(source: Path) -> list[float]:
    md5 = _file_md5(source)   # use existing core.qa.hashes.md5_file
    cached = KEYFRAME_CACHE / f"{md5}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    keyframes = _scan_keyframes(source)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(keyframes))
    return keyframes
```

### `core/qa/phash.py` — adaptive samples

```python
def compare(input_path: Path, output_path: Path, n: int | None = None) -> PHashStats:
    if n is None:
        duration = _probe_duration(input_path)
        n = max(60, min(600, int(duration / 60 * 30)))  # ~30 frames/min, cap 600
    # … rest unchanged
```

### `core/qa/vmaf.py` — `subsample`

```python
def compute(input_path: Path, output_path: Path, *,
            threads: int = 4, subsample: int = 1) -> VMAFResult:
    libvmaf_args = f"libvmaf=n_threads={threads}"
    if subsample > 1:
        libvmaf_args += f":n_subsample={subsample}"
    # …
```

CLI: `yt-uniq qa --vmaf-subsample 5` (каждый 5-й кадр).

### `core/orchestrator.py` — workers + cancel honoured in parallel

`RunOptions.workers: int = 1`. `run_full` пробрасывает в segmenter.
Cancel токен полу-блокирующе ждёт текущие воркеры (≤30 сек) перед SIGKILL.

## Acceptance

**Manual (не CI):**

```bash
# 1. Baseline single-pass ffmpeg для сравнения.
time ffmpeg -i ~/movies/2h_1080p.mp4 -c:v libx264 -preset slow -crf 18 \
            -c:a aac -b:a 256k /tmp/baseline.mp4

# 2. yt-uniquifier на том же файле.
python tools/benchmark.py ~/movies/2h_1080p.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out /tmp/uniq.mp4 \
  --workers 4

# Acceptance:
# - wall_sec ≤ 1.5 × baseline
# - rss_peak_kb < 4 GB
# - cancel via Ctrl+C — все воркеры завершаются за <10 сек, no zombies

# 3. Resume after kill (на половине файла) даёт байт-эквивалентный output.
yt-uniq run … --keep-segments &
sleep 600
kill -INT $!
yt-uniq run … --keep-segments       # resume
# Diff с no-kill прогоном: chromaprint Jaccard > 0.99

# 4. Seam test.
python tools/seam_test.py /tmp/uniq.mp4 --work-dir .yt_uniq_work/<hash>
# No seams above 0.005 delta

# 5. HDR scale test.
python tools/benchmark.py ~/movies/30min_4k_hdr.mkv \
  --profile src/yt_uniquifier/profiles/medium_hdr.yaml \
  --encoder hevc_videotoolbox \
  --out /tmp/uniq_hdr.mp4
# Output: HDR preserved, VMAF in HDR mode > 88
```

**Automated (CI):**

```bash
pytest -q tests/unit/test_segmenter_long_input.py
pytest -q tests/unit/test_keyframe_cache.py
pytest -q tests/unit/test_parallel_segments.py
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_segmenter_long_input.py` | mock list_keyframes для 7200 сек → plan_segments возвращает 10–15 segments at target=600 |
| Unit | `tests/unit/test_keyframe_cache.py` | первый list_keyframes пишет cache; второй — cache hit (mock subprocess не вызывается) |
| Unit | `tests/unit/test_parallel_segments.py` | mock encoder=libx264 + workers=2 → ProcessPoolExecutor вызван; encoder=h264_nvenc + workers=2 → silent fallback к sequential |
| Unit | `tests/unit/test_phash_adaptive_n.py` | duration=3600 → n=600 (capped); duration=10 → n=60 (floor) |
| Unit | `tests/unit/test_vmaf_subsample.py` | subsample=5 → присутствует `:n_subsample=5` в lavfi-строке |
| Manual / Runbook | `docs/runbook_scale_test.md` | пошаговая инструкция для проверки на реальном фильме (не CI) |

## Risks

| Риск | Митигация |
|---|---|
| Длительный VMAF (>15 мин на 2h файле) — пользователь думает «висит» | `--vmaf-subsample 5` дефолт для файлов > 30 мин; CLI печатает «VMAF in progress…» |
| ProcessPool на macOS — `fork()` issues с PyQt6 в общем модуле | gui не импортируется в воркеры (бенчмарк не использует gui); если import case-сится — добавить `multiprocessing.set_start_method("spawn")` |
| Кеш keyframes раздувается на больших корпусах | TTL 30 дней + cap размером 500 entries |
| Workers конкурируют за ffmpeg threads | `OMP_NUM_THREADS` и `-threads N` явно ограничиваются: `total_threads / workers` |
| Параллельный encoding ломает воспроизводимость x264 (slice-threads vs frame-threads) | Использовать `-threads 1` в каждом воркере — детерминизм важнее single-segment speed |
| HDR roundtrip даёт неестественно низкий VMAF | VMAF HDR-режим (`phone_model=0` или `hdr_model=1` в новых libvmaf) — добавить флаг `--vmaf-hdr` |

## Hand-off (release v0.2.0)

После Phase 10:
- Pipeline валидирован на реальных длинных файлах.
- `tools/benchmark.py` и `tools/seam_test.py` — постоянные части repo для
  периодической проверки.
- Performance optimizations applied: keyframe-cache, optional workers,
  adaptive QA sampling.
- `docs/runbook_scale_test.md` — для регрессионных проверок при обновлениях.
- Готово к `git tag v0.2.0`.

## Что отложено в v0.3 (явно)

- HDR → SDR tonemap.
- Параллельный GPU encoding (VRAM partition).
- Авто-выбор `seed_strategy` (heuristic по типу контента).
- Distributed batch по нескольким машинам.
- HDR-aware VMAF metric (отдельная calibration).
- Image-based subtitle OCR + re-render (для Blu-ray PGS).
