# Spec 02 — Transforms, Pipeline, Runner

> **Phase 2** · 3-4 дня · **Deps:** [01-probe-encoder-models](./01-probe-encoder-models.md)

## Goal

`yt-uniq run <input> --profile profiles/medium.yaml --out <output>` обрабатывает короткое видео одним вызовом ffmpeg (один `-filter_complex`, без bgr24 через Python stdin). Все базовые трансформации видео + аудио работают, multi-track audio/subs/chapters проходят насквозь.

**Сегментация и resume — не в этой фазе.** Здесь обрабатываем как один монолитный вызов. Для длинных файлов будет работать, но без устойчивости к kill — это решается в Spec 03.

## Scope

**In:**

- `core/transforms/base.py` — `TransformSpec`, `LabelAllocator`, `FilterChain`, registry.
- `core/transforms/*.py` — 9 трансформаций (5 видео + 3 аудио + blend_b).
- `core/pipeline.py` — `FilterGraph.build()` собирает один `BuiltCommand`.
- `core/runner.py` — `run()` запускает ffmpeg, парсит `-progress pipe:1`, обрабатывает cancel.
- `cli/cmd_run.py` — typer-команда + rich-progress.
- `profiles/{soft,medium,aggressive,legacy_ab}.yaml` — готовые наборы трансформов.

**Not in:** сегментация, resume, checkpoint, metadata-args (минимальные только), preflight, QA. Всё это — Spec 03/04.

## Modules

### `core/transforms/base.py`

```python
from dataclasses import dataclass
from typing import Callable, Literal, Protocol
from pydantic import BaseModel

class LabelAllocator:
    """Раздаёт уникальные ffmpeg-лейблы: vN, aN."""
    def __init__(self) -> None:
        self._v = 0
        self._a = 0
    def next(self, kind: Literal["v", "a"]) -> str: ...

@dataclass(frozen=True)
class FilterChain:
    """Готовый кусок filter_complex: 'in_label] FILTERS [out_label'."""
    in_label: str
    out_label: str
    filter_str: str          # без leading [in] и trailing [out]
    extra_inputs: tuple[str, ...] = ()   # для blend_b: ('B.mp4',)

class TransformParamsProto(Protocol):
    """Каждый transform определяет свой pydantic-class для params."""

BuildFn = Callable[[BaseModel, LabelAllocator, str], FilterChain]
# args: (params, allocator, in_label) -> FilterChain

@dataclass(frozen=True)
class TransformSpec:
    id: str                   # 'video.crop_resize'
    kind: Literal["video", "audio"]
    schema: type[BaseModel]
    build: BuildFn
    defaults: dict
    incompatible_with: tuple[str, ...] = ()

# Глобальный registry
REGISTRY: dict[str, TransformSpec] = {}

def register(spec: TransformSpec) -> None:
    if spec.id in REGISTRY:
        raise ValueError(f"transform {spec.id} already registered")
    REGISTRY[spec.id] = spec

def get(id: str) -> TransformSpec:
    if id not in REGISTRY:
        raise KeyError(f"unknown transform: {id}")
    return REGISTRY[id]
```

### `core/transforms/video_geom.py`

```python
class CropResizeParams(BaseModel):
    max_strength: float = 0.03   # макс доля кропа с каждой стороны (0.01-0.10)
    rng_seed: int | None = None

def _build_crop_resize(params: CropResizeParams, alloc: LabelAllocator, in_lbl: str) -> FilterChain:
    rng = random.Random(params.rng_seed)
    l = rng.uniform(0, params.max_strength)
    r = rng.uniform(0, params.max_strength)
    t = rng.uniform(0, params.max_strength)
    b = rng.uniform(0, params.max_strength)
    out = alloc.next("v")
    cw = 1 - l - r
    ch = 1 - t - b
    f = (f"crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{l:.4f}:ih*{t:.4f},"
         f"scale=iw/{cw:.4f}:ih/{ch:.4f}:flags=lanczos")
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=f)

register(TransformSpec(
    id="video.crop_resize", kind="video", schema=CropResizeParams,
    build=_build_crop_resize, defaults={"max_strength": 0.03},
))

class RotateParams(BaseModel):
    degrees: float = 0.1     # знак рандомизируется, если rng_seed задан
    rng_seed: int | None = None
# build: rotate=<rad>*PI/180:fillcolor=black,scale=iw:ih (crop overflow)
```

### `core/transforms/video_color.py`

```python
class ColorEqParams(BaseModel):
    brightness: float = 0.01    # ±N
    contrast: float = 1.02      # multiplier
    gamma: float = 0.99
    saturation: float = 1.03
# build: eq=brightness=...:contrast=...:gamma=...:saturation=...
```

### `core/transforms/video_noise.py`

```python
class NoiseParams(BaseModel):
    strength: int = 4           # 1-10
# build: noise=alls={strength}:allf=t+u
```

### `core/transforms/video_blend.py` (порт легаси AB)

```python
class BlendBParams(BaseModel):
    b_video_path: Path
    opacity: float = 0.03       # 0.01-0.15
# build: extra_inputs=('B.mp4',), фильтр-цепочка:
#   [in_lbl][1:v]scale2ref=w=iw:h=ih[b_scaled][a_ref];
#   [a_ref][b_scaled]blend=all_expr='A*(1-O)+B*O'[out]
# Pipeline увидит extra_inputs и добавит соответствующий -i и переиндексирует ссылки.
```

### `core/transforms/video_speed.py`

```python
class SpeedParams(BaseModel):
    rate: float = 1.0           # 0.97..1.03
# build: setpts=PTS/{rate}
# ВАЖНО: тот же rate должен быть передан в audio_pitch (Pipeline следит)
```

### `core/transforms/audio_pitch.py`

```python
class PitchTempoParams(BaseModel):
    pitch: float = 1.005    # 0.97..1.03
    tempo: float = 1.0      # 0.97..1.03 (комбинируется с video.speed.rate)
    sample_rate: int = 48000

def _build(params, alloc, in_lbl):
    out = alloc.next("a")
    compensate = params.tempo / params.pitch
    # atempo limit per-instance [0.5, 2.0] на старых ffmpeg
    chain = _cascade_atempo(compensate)
    f = f"asetrate={params.sample_rate}*{params.pitch:.6f},aresample={params.sample_rate},{chain}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=f)

def _cascade_atempo(target: float) -> str:
    """Если target вне [0.5, 2.0] — цепочка atempo=X,atempo=Y,..."""
```

### `core/transforms/audio_eq.py`

```python
class AudioEqParams(BaseModel):
    bands: list[tuple[float, float]] = [(120, -0.6), (4500, 0.4)]   # (freq, gain_db)
# build: equalizer=f=120:t=q:w=1:g=-0.6,equalizer=f=4500:t=q:w=1:g=0.4
```

### `core/transforms/audio_loudnorm.py`

```python
class LoudnormParams(BaseModel):
    integrated: float = -14.0   # YouTube target
    true_peak: float = -1.5
    lra: float = 11.0

class LoudnormMeasurement(BaseModel):
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

def measure(source_path: Path) -> LoudnormMeasurement:
    """First-pass: ffmpeg -i src -af loudnorm=I=...:print_format=json -f null -; parse JSON tail."""

def build_second_pass(params, m: LoudnormMeasurement, alloc, in_lbl) -> FilterChain:
    """loudnorm=I=...:TP=...:LRA=...:measured_I=...:measured_TP=...:measured_LRA=...:measured_thresh=...:offset=...:linear=true:print_format=summary"""
```

Особенность: `audio_loudnorm` требует **двухпроходного** ffmpeg. `Pipeline.build()` детектит присутствие, запускает `measure()` отдельно перед основным проходом, кеширует результат в `work_dir/loudnorm.json` (переиспользуется при resume в Spec 03).

### `core/pipeline.py`

```python
from dataclasses import dataclass
from yt_uniquifier.core.models import Plan, EncoderCandidate

@dataclass(frozen=True)
class BuiltCommand:
    args: list[str]                  # полная ffmpeg-команда кроме output_path
    filter_complex: str
    output_video_label: str          # '[vout]'
    output_audio_label: str | None   # '[aout]'
    passthrough_audio_maps: list[str]   # ['-map', '0:a:1?', '-c:a:1', 'copy', ...]
    passthrough_sub_maps: list[str]
    extra_inputs: list[Path]         # для blend_b

class FilterGraph:
    def __init__(self, plan: Plan, output: Path, work_dir: Path) -> None: ...
    def build(self) -> BuiltCommand: ...
```

Алгоритм `build()`:

1. `LabelAllocator()` создан.
2. Video-цепочка: вход = `[0:v:0]`, проходит по `plan.profile.transforms` где `kind=="video"`, склеивает `FilterChain` через `;`. Финальный label = `output_video_label`. Перед последним — `format=yuv420p` (или `yuv420p10le` для HDR + keep_hdr).
3. Audio-цепочка для основной дорожки: `[0:a:0]` → audio transforms → `[aout]`. Если в transforms есть `audio.loudnorm` — сначала вызывается `measure(plan.source.path)`, результат подставляется в фильтр.
4. Passthrough: для остальных audio (`plan.profile.audio_tracks` определяет), для subs (если не image-based), для chapters.
5. Encoder-args: по `plan.encoder.vendor`:
   - `nvenc`: `-c:v <name> -preset p6 -rc vbr -cq 19 -b:v 0 -maxrate <1.25×src> -bufsize <2×maxrate>`
   - `qsv`: `-c:v <name> -global_quality 19 -look_ahead 1`
   - `amf`: `-c:v <name> -rc cqp -qp_i 19 -qp_p 19`
   - `videotoolbox`: `-c:v <name> -q:v 50`
   - `x264/x265`: `-c:v <name> -preset slow -crf 18`
6. Audio out: `-c:a:0 aac -b:a:0 256k` (для основной дорожки), `-c:a:N copy` для остальных.
7. Container: `-movflags +faststart`, `-map_metadata -1`, минимальные `-metadata encoder="yt-uniquifier/{version}"`.
8. HDR: если `plan.source.video[0].color.is_hdr` и `plan.profile.keep_hdr=True` — прокидывает `-color_primaries`, `-color_trc`, `-colorspace`, `-color_range`.

### `core/runner.py`

```python
import asyncio, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

@dataclass
class RunEvent:
    kind: Literal["progress", "log", "done", "error"]
    payload: dict   # progress: {out_time_us, frame, fps, speed}; log: {line, level}

@dataclass
class RunResult:
    returncode: int
    duration_sec: float
    output_path: Path

class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False
    def cancel(self) -> None: self._cancelled = True
    def is_cancelled(self) -> bool: return self._cancelled

def run(cmd: BuiltCommand, *, output: Path,
        on_event: Callable[[RunEvent], None],
        cancel_token: CancelToken | None = None,
        log_path: Path | None = None) -> RunResult: ...
```

Реализация: запускаем ffmpeg с `-progress pipe:1 -nostats`. Парсим stdout по строкам `key=value`. На каждый блок (заканчивается `progress=continue` или `progress=end`) эмитим `RunEvent(kind="progress", ...)`. Stderr → log_path (RotatingFileHandler). Cancel: SIGINT → wait 5s → SIGKILL.

### `cli/cmd_run.py`

```python
@app.command("run")
def run_cmd(
    input: Path = typer.Argument(..., exists=True),
    profile: Path = typer.Option(..., "--profile"),
    output: Path = typer.Option(..., "--out"),
    encoder: str | None = typer.Option(None, help="Override auto-pick (e.g. h264_nvenc)"),
    work_dir: Path = typer.Option(Path("./.yt_uniq_work"), help="Temp/checkpoint dir"),
) -> None: ...
```

UX: `rich.Progress` с одним bar по `out_time_us / total_duration_us`, плюс строка speed/fps. Ctrl+C → cancel_token.cancel().

### Профили (`profiles/medium.yaml`)

```yaml
name: medium
description: Balanced uniqueness vs quality. ~VMAF 92.
transforms:
  - id: video.crop_resize
    enabled: true
    params: {max_strength: 0.02, rng_seed: 42}
  - id: video.color_eq
    enabled: true
    params: {brightness: 0.012, contrast: 1.018, gamma: 0.995, saturation: 1.03}
  - id: video.noise
    enabled: true
    params: {strength: 4}
  - id: video.rotate
    enabled: false
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.0008, tempo: 1.0}
  - id: audio.eq
    enabled: true
  - id: audio.loudnorm
    enabled: true
audio_tracks: first
keep_hdr: false
target_codec: h264
target_loudness_lufs: -14.0
seed: 42
```

Аналогично `soft.yaml` (минимальные сдвиги), `aggressive.yaml` (бóльшие), `legacy_ab.yaml` (только `video.blend_b` для совместимости со старым поведением).

## Acceptance

```bash
# 1. Run short clip через medium
yt-uniq run tests/fixtures/sample_2s.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out /tmp/out.mp4
# Должен закончиться без ошибок, файл валидный mp4, длительность ~2s.

# 2. Multi-track passthrough
ffprobe -v error -show_streams tests/fixtures/multi_audio_2s.mp4 -of json | jq '.streams | length'
# 4 (1 video, 2 audio, 1 subs)
yt-uniq run tests/fixtures/multi_audio_2s.mp4 --profile ...soft.yaml --out /tmp/multi.mp4
ffprobe -v error -show_streams /tmp/multi.mp4 -of json | jq '.streams | length'
# 4 — все дорожки на месте

# 3. Cancel
yt-uniq run long.mp4 --profile ...medium.yaml --out /tmp/long.mp4 &
sleep 5; kill -INT $!
# Завершается за <10 сек, файл не валидный (ок — частичный), нет zombie ffmpeg.

# 4. Encoder override
yt-uniq run sample.mp4 --profile ...soft.yaml --encoder libx264 --out /tmp/cpu.mp4
ffprobe -v error /tmp/cpu.mp4 -show_streams | grep codec_name
# codec_name=h264
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_label_allocator.py` | next("v") → v1, v2, ...; независимые счётчики |
| Unit | `tests/unit/test_transform_video_geom.py` | crop_resize с фиксированным seed → детерминированная строка |
| Unit | `tests/unit/test_transform_audio_pitch.py` | atempo каскад при выходе из [0.5, 2.0] |
| Unit | `tests/unit/test_transform_audio_loudnorm.py` | парсинг JSON-вывода measurement |
| Unit | `tests/unit/test_pipeline_graph.py` | snapshot: medium-профиль на фикстурном SourceMeta → строка filter_complex совпадает |
| Unit | `tests/unit/test_pipeline_graph.py` | passthrough_audio_maps корректны при `audio_tracks: "all"` для 3-аудио источника |
| Unit | `tests/unit/test_pipeline_graph.py` | HDR-источник + keep_hdr=True → присутствуют `-color_primaries` etc. |
| Unit | `tests/unit/test_pipeline_graph.py` | blend_b → extra_inputs не пустой, -i B.mp4 в args |
| Unit | `tests/unit/test_runner_progress.py` | mock ffmpeg subprocess, проверяем парсинг -progress lines, эмит RunEvent |
| Integration | `tests/integration/test_run_short_clip.py` | реальный ffmpeg на testsrc2 2s, все 4 профиля, проверяем что output открывается ffprobe |
| Integration | `tests/integration/test_run_multitrack.py` | 2-аудио + soft sub источник → passthrough дорожек байт-в-байт через ffprobe streams |
| Integration | `tests/integration/test_run_cancel.py` | старт + SIGINT через 1с → graceful exit, нет zombie |

Фикстуры (расширение `tests/conftest.py` из Spec 01):
- `multi_audio_clip(tmp_path)` — testsrc2 + 2 sine с разными частотами как 2 audio tracks + srt-сабтитры.
- `hdr_clip(tmp_path)` — testsrc2 + `-color_primaries bt2020 -color_trc smpte2084 -pix_fmt yuv420p10le`.

## Risks

- **Snapshot-тесты filter_complex хрупкие.** Решение: фиксированный rng_seed во всех transforms; snapshot хранится в `tests/snapshots/*.txt`, обновление через `pytest --snapshot-update` (если используем `syrupy`) или вручную (если plain strings).
- **`audio.loudnorm` двухпроходный — медленно.** На фазе 02 пока не оптимизируем (на 2-сек клипе незаметно). В Spec 03 кеш `loudnorm.json` решит проблему повторных прогонов.
- **`blend_b` требует B-видео той же длительности или длиннее.** Pipeline должен валидировать через probe(b_video_path) и понятно падать иначе.
- **NVENC `-cq` поведение разное на старых драйверах.** Параметры заданы по docs для CUDA 12+; для старых драйверов fallback на libx264 через `pick_encoder`.

## Hand-off в Spec 03

После Phase 2:
- `FilterGraph.build()` готов и протестирован.
- `runner.run()` умеет endless single-process ffmpeg с прогрессом и cancel.
- `BuiltCommand` — готовый кирпич, который Spec 03 использует **на сегмент**, а не на весь файл.
- Профили созданы и работают.
- `audio.loudnorm.measure()` вынесена как отдельная функция — Spec 03 закеширует её вывод.
