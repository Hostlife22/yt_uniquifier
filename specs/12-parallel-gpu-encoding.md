# Spec 12 — Parallel GPU Encoding

> **Phase 12 (v0.3)** · 2 дня · **No deps** (parallel-safe with 11, 13)

## Goal

На NVIDIA consumer-картах (RTX 3060+) запускать 2–3 параллельных
NVENC-сессии (per-driver лимит). На VideoToolbox / QSV / AMF — 2–4. Лимит
определяется автоматически (по querk-у VRAM + driver hints), не
угадывается пользователем. `--workers 8` на consumer NVENC молча
даунгрейдится до 3 c log-сообщением.

## Scope

**In:**

- `core/encoder.py`: `EncoderCandidate.max_parallel: int` (новое поле).
- `core/encoder._detect_max_parallel(vendor)` — per-vendor heuristic.
- `core/segmenter.parallel_safe(plan)` теперь возвращает `int` (не `bool`)
  — максимум параллельных сегментов для плана.
- `core/segmenter.process_video_segments_parallel`: capping через
  `min(requested_workers, plan.encoder.max_parallel)`.
- `core/runner.py`: catch NVENC OOM (returncode 24 + stderr match) → retry
  с back-off 2s и `force_workers=1`.
- Docs: пометка в `docs/profiles.md` про NVENC consumer ограничение
  на 3 сессии (драйвер).

**Not in:** разделение одного сегмента между GPU и CPU; multi-GPU
dispatch (`CUDA_VISIBLE_DEVICES` round-robin — v0.4); MIG (NVIDIA A100+
slicing).

## Modules

### `core/encoder.py` — расширение

```python
class EncoderCandidate(BaseModel):
    name: str
    vendor: EncoderVendor
    codec: EncoderKind
    works: bool
    error: str | None = None
    # NEW (v0.3): how many concurrent encode sessions are safe.
    max_parallel: int = Field(default=1, ge=1, le=16)


# Per-vendor consumer-tier defaults — used when we cannot query the driver.
_VENDOR_DEFAULT_PARALLEL: dict[EncoderVendor, int] = {
    "nvenc": 3,            # consumer NVENC driver limit
    "qsv": 2,
    "amf": 2,
    "videotoolbox": 2,
    "x264": 0,             # 0 = use cpu_count() // 2
    "x265": 0,
}


def _detect_max_parallel(vendor: EncoderVendor) -> int:
    """Best-effort detection of how many concurrent encode sessions are safe."""
    if vendor == "nvenc":
        return _nvenc_max_parallel()
    if vendor in ("x264", "x265"):
        import os
        return max(1, (os.cpu_count() or 2) // 2)
    return _VENDOR_DEFAULT_PARALLEL.get(vendor, 1)


def _nvenc_max_parallel() -> int:
    """Parse nvidia-smi output; cap at 3 for consumer, 8 for pro/Quadro."""
    try:
        proc = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.free,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    if proc.returncode != 0:
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    # First GPU only. Format: "<free_mb>, <name>"
    first_line = proc.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first_line.split(",", 1)]
    if len(parts) != 2:
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    try:
        free_mb = int(parts[0])
    except ValueError:
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    name = parts[1].lower()
    # Pro / datacenter chips have no software session limit.
    is_pro = any(t in name for t in ("quadro", "rtx a", "tesla", "a100", "h100", "l40"))
    cap = 8 if is_pro else 3
    # ~500 MB per 1080p session.
    by_vram = max(1, free_mb // 500)
    return min(cap, by_vram)
```

### `core/segmenter.py` — изменение сигнатуры

```python
def parallel_safe(plan: Plan) -> int:
    """Return max concurrent encode workers safe for this plan's encoder."""
    return plan.encoder.max_parallel or 1


def process_video_segments_parallel(
    pending, plan, work_dir, *, workers=1, on_event=None,
    cancel_token=None, on_segment_done=None,
):
    cap = parallel_safe(plan)
    effective = min(max(1, workers), cap)
    if effective < workers:
        if on_event:
            on_event(RunEvent(kind="log", payload={
                "phase": "workers",
                "message": f"workers downgraded {workers} → {effective} "
                           f"({plan.encoder.name} cap)",
            }))
    # … rest of function uses `effective` instead of `workers` …
```

### `core/runner.py` — NVENC OOM retry

```python
_NVENC_OOM_PATTERNS = (
    "OpenEncodeSessionEx failed",
    "out of memory",
    "no encode capable devices",
)

def run(cmd, *, output, on_event=None, cancel_token=None,
        log_path=None, progress_via_stdout=True,
        _retried: bool = False):
    # … existing implementation …
    # After waiting for process, before raising PipelineError:
    if rc != 0 and _is_nvenc_oom(log_lines) and not _retried:
        if on_event:
            on_event(RunEvent(kind="log", payload={
                "phase": "retry", "reason": "nvenc oom",
            }))
        time.sleep(2)
        return run(cmd, output=output, on_event=on_event,
                   cancel_token=cancel_token, log_path=log_path,
                   progress_via_stdout=progress_via_stdout, _retried=True)
    # … rest of error handling …


def _is_nvenc_oom(log_lines: list[str]) -> bool:
    tail = "\n".join(log_lines[-50:]).lower()
    return any(p in tail for p in (s.lower() for s in _NVENC_OOM_PATTERNS))
```

### `core/encoder.py.detect_encoders` — заполнить max_parallel

```python
def detect_encoders(force: bool = False) -> list[EncoderCandidate]:
    # … existing nullsrc test-run loop …
    results = []
    for name, vendor, codec in _CANDIDATES:
        works, error = _test_run(name)
        max_par = _detect_max_parallel(vendor) if works else 1
        results.append(EncoderCandidate(
            name=name, vendor=vendor, codec=codec,
            works=works, error=error, max_parallel=max_par,
        ))
    _save_cache(version_key, results)
    return results
```

## Acceptance

```bash
# On a machine with NVENC available:
yt-uniq probe --encoders --refresh | jq '.[] | select(.name=="h264_nvenc")'
# {
#   "name": "h264_nvenc", "vendor": "nvenc", "codec": "h264",
#   "works": true, "error": null,
#   "max_parallel": 3                                            # NEW
# }

# Run with workers=8 on NVENC → downgraded to 3.
yt-uniq run movie.mp4 \
  --profile profiles/cid_aware.yaml \
  --out out.mp4 \
  --encoder h264_nvenc \
  --workers 8 \
  --keep-segments 2>&1 | grep workers
# [workers] workers downgraded 8 → 3 (h264_nvenc cap)

# Wall time ~ 1/3 × single-NVENC baseline (verified via tools/benchmark.py).

# On a non-NVIDIA Mac:
yt-uniq probe --encoders | jq '.[] | select(.works) | {name, max_parallel}'
# [{"name": "h264_videotoolbox", "max_parallel": 2},
#  {"name": "libx264", "max_parallel": 6},   # 12-core M2 / 2
#  ...]
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_encoder_max_parallel.py` | mock nvidia-smi: 12 GB free + "RTX 3070" → cap=3; "Quadro RTX 8000" → cap=8; nvidia-smi missing → fallback 3 |
| Unit | `tests/unit/test_encoder_max_parallel.py` | x264 → cpu_count() // 2; videotoolbox → 2 |
| Unit | `tests/unit/test_parallel_segments_cap.py` | workers=8 + max_parallel=3 → ThreadPool size 3 |
| Unit | `tests/unit/test_parallel_segments_cap.py` | workers=2 + max_parallel=8 → workers wins (2) |
| Unit | `tests/unit/test_parallel_segments_cap.py` | emit log "workers downgraded N → M" |
| Unit | `tests/unit/test_runner_nvenc_retry.py` | mock Popen: rc=24 + "OpenEncodeSessionEx" → second call rc=0 succeeds; no infinite loop |
| Unit | `tests/unit/test_runner_nvenc_retry.py` | non-OOM error doesn't trigger retry |
| Integration | `tests/integration/test_real_parallel_nvenc.py` | `@pytest.mark.skipif(not has_nvenc())` — workers=4 на 4 коротких сегментах, timestamps файлов перекрываются (concurrent), not sequential |

## Risks

| Риск | Митигация |
|---|---|
| Consumer NVENC unpatched лимит = 3, не 8 (драйверный rule) | дефолт `max_parallel=3` для consumer; патч-инструкция в docs (no commercial distribution) |
| nvidia-smi missing (Linux Docker без NVIDIA tools, AMD-only хост с видимым NVENC?) | fallback на 2 + warn в первом запуске |
| Apple M-series VideoToolbox имеет внутреннюю serialization (фактический speedup <2× на 2 workers) | эмпирический detect: при инициализации запускаем 2 параллельных testsrc encodes, если wall_time ≈ 2× sequential → max_parallel=1. Кешируем в encoders.json |
| AMF на Linux частично работает | как сейчас — test_run на nullsrc решает; max_parallel=2 по дефолту |
| OOM retry бесконечно loops если оба прогона по одной и той же причине OOM | `_retried` flag отключает второй retry; после второго неудачного → PipelineError |
| NVENC сессии используются другими приложениями (OBS / browser hardware video) | nvidia-smi не учитывает другие процессы; runner отлавливает OOM на старте сегмента и логирует; пользователь видит downgrade |
| Двойной перекодинг при retry удвоит wall_time | acceptable trade-off; альтернатива — сразу падать |

## Hand-off

После Phase 12:
- `EncoderCandidate.max_parallel` доступен в Plan; orchestrator и
  segmenter автоматически используют.
- `--workers N` в CLI работает корректно для всех вендоров (просто
  capping).
- Бенчмарк (`tools/benchmark.py`) показывает реальный 2-3× speedup на
  NVENC consumer / CPU 4+ ядер.
- Phase 13 (distributed) использует один worker per machine, не
  внутримашинный параллелизм — но max_parallel у каждого worker'а
  автоматически работает.
