# Spec 03 — Segmenter, Resume, Metadata, Preflight

> **Phase 3** · 2-3 дня · **Deps:** [02-transforms-pipeline-runner](./02-transforms-pipeline-runner.md) · **Parallel:** [04-qa-report](./04-qa-report.md)

## Goal

Убить процесс на 50% 2-часового файла → перезапустить с теми же аргументами → продолжит с последнего готового сегмента, итоговый файл байт-эквивалентен непрерывному прогону.

`yt-uniq preflight <input> --profile <p>` проверяет совместимость с YouTube targets и HDR.

## Scope

**In:**

- `core/segmenter.py` — split keyframe-aware, per-segment process, concat demuxer.
- `core/checkpoint.py` — atomic `state.json`, resume логика.
- `core/metadata.py` — построение `-metadata`/`-map_metadata` аргументов.
- `core/preflight.py` — матрица YouTube-targets + HDR-валидация.
- `cli/cmd_preflight.py` — отдельная команда.
- `cli/cmd_run.py` дорабатывается: использует segmenter вместо одиночного pipeline-run.

**Not in:** параллельная обработка сегментов (v1.1), варьируемые параметры по сегментам (v1.1), distributed batch.

## Modules

### `core/segmenter.py`

```python
from pathlib import Path
from typing import Callable
from yt_uniquifier.core.models import Plan, Segment
from yt_uniquifier.core.runner import RunEvent

def plan_segments(plan: Plan, target_size_sec: float = 600.0) -> list[Segment]:
    """
    1. ffprobe -select_streams v -skip_frame nokey -show_frames -show_entries frame=pkt_pts_time
       -> keyframe timestamps.
    2. Greedy: накапливаем длительность от keyframe до keyframe, отрезаем когда >= target.
    3. Возвращаем list[Segment] с status=pending.
    """

def stream_copy_extract(segment: Segment, source: Path, dest: Path) -> None:
    """ffmpeg -ss {start} -to {end} -i {source} -c copy -avoid_negative_ts make_zero {dest}"""

def process_video_segment(
    segment: Segment,
    plan: Plan,
    work_dir: Path,
    loudnorm_measurement: LoudnormMeasurement | None,   # для passthrough audio в сегменте
    on_event: Callable[[RunEvent], None],
) -> Path:
    """
    1. stream_copy_extract -> seg_NNNN_src.mkv (контейнер mkv — устойчивее к concat).
    2. FilterGraph.build() на этом сегменте, НО:
       - audio_loudnorm пропускается (основная audio обрабатывается отдельно вне сегментации);
       - audio_pitch_tempo пропускается (по той же причине);
       - audio passthrough дорожки идут как обычно (-c:a copy).
       - video transforms — все.
    3. runner.run() -> seg_NNNN.mkv.
    """

def process_main_audio(
    plan: Plan,
    work_dir: Path,
    on_event: Callable[[RunEvent], None],
) -> Path:
    """
    Обрабатывает ТОЛЬКО основную audio-дорожку (0:a:0) на ПОЛНОМ исходнике.
    Применяет audio.pitch_tempo, audio.eq, audio.loudnorm (с подставленным measurement).
    Выход: work_dir/main_audio.m4a (AAC 256k).
    Делается ОДИН раз для всего файла — корректный loudnorm без артефактов на швах.
    """

def concat_segments(
    segments: list[Path],
    main_audio: Path,
    plan: Plan,
    output: Path,
    metadata_args: list[str],
) -> None:
    """
    1. concat_list.txt с относительными путями.
    2. ffmpeg -f concat -safe 0 -i concat_list.txt -i main_audio
         -map 0:v -map 1:a:0
         -map 0:a:1? -c:a:1 copy (passthrough из сегментов)
         -map 0:s? -c:s copy
         -map_chapters 0
         -c:v copy -c:a:0 copy
         -movflags +faststart
         {metadata_args}
         output.mp4
    """
```

**Ключевая идея разделения видео/основное аудио:**

- Видео сегментируется и обрабатывается покусочно → resume-friendly.
- Основная audio-дорожка обрабатывается **целиком на полном файле**, отдельно. Loudnorm двухпроходный (measure + apply) корректен только на целостной аудио-дорожке; pitch shift через `asetrate+aresample+atempo` имеет переходные процессы, которые на швах сегментов давали бы щелчки.
- Passthrough audio-дорожки и subs сегментируются вместе с видео через `-c:a copy` / `-c:s copy` — они не перекодируются, артефактов нет.

### `core/checkpoint.py`

```python
import json, os
from pathlib import Path
from yt_uniquifier.core.models import Segment, Plan

class CheckpointStore:
    def __init__(self, work_dir: Path, plan: Plan) -> None:
        self.work_dir = work_dir
        self.state_path = work_dir / "state.json"
        self.plan = plan

    def init_or_resume(self, segments: list[Segment]) -> list[Segment]:
        """
        Если state.json существует и plan_hash совпадает — загружаем сегменты, возвращаем их.
        Если plan_hash отличается — инвалидируем (бэкап + начинаем заново).
        Если state.json нет — пишем новый со всеми pending.
        """

    def mark(self, idx: int, status: Literal["in_progress","done","failed"],
             out_path: Path | None = None) -> None:
        """Atomic update одного сегмента: read → mutate → tmp write → rename."""

    def all_done(self) -> bool: ...
    def pending(self) -> list[Segment]: ...
```

`state.json` структура:

```json
{
  "schema_version": 1,
  "tool_version": "0.1.0a0",
  "input_md5": "abc...",
  "plan_hash": "def...",
  "loudnorm_measurement": {"input_i": -23.4, "input_tp": -1.2, ...},
  "segments": [
    {"idx": 0, "start_sec": 0.0, "end_sec": 612.3, "status": "done",
     "src_path": "seg_0000_src.mkv", "out_path": "seg_0000.mkv"},
    {"idx": 1, "start_sec": 612.3, "end_sec": 1220.5, "status": "pending",
     "src_path": null, "out_path": null}
  ],
  "main_audio_path": null
}
```

Атомарность: `write(state.json.tmp); os.replace(state.json.tmp, state.json)`.

### `core/metadata.py`

```python
from yt_uniquifier.core.models import SourceMeta, Plan, Profile

def build_metadata_args(
    source: SourceMeta,
    plan: Plan,
    title_template: str | None = None,
) -> list[str]:
    """
    Возвращает: [
      '-map_metadata', '-1',
      '-metadata', 'encoder=yt-uniquifier/{version}',
      '-metadata', 'creation_time=<iso>',
      '-metadata', f'title={resolved_title}',
      '-metadata:s:v:0', 'language=...',
      '-metadata:s:a:0', 'language=...',
    ]
    """

def resolve_title(template: str, source: SourceMeta, profile: Profile) -> str:
    """Поддерживает {stem}, {date}, {profile}, {hash8}."""
```

`-map_metadata -1` чистит ВСЁ из исходника; затем явно добавляем что нужно. Это убирает encoder-tag, comment, software-tag — что важно для уникализации (метаданные тоже входят в MD5/hash).

### `core/preflight.py`

```python
from typing import Literal
from pydantic import BaseModel
from yt_uniquifier.core.models import SourceMeta, Plan, EncoderCandidate

Severity = Literal["ok", "warn", "fail"]

class PreflightFinding(BaseModel):
    code: str
    severity: Severity
    message: str
    suggestion: str | None = None

# YouTube recommended targets (docs.google.com/youtube/help/answer/4603579)
YT_TARGETS = {
    "container": {"mp4", "mov"},
    "video_codec": {"h264", "hevc", "vp9", "av1"},
    "audio_codec": {"aac", "opus"},
    "fps": {23.976, 24, 25, 29.97, 30, 50, 59.94, 60},
    "audio_sample_rate": {44100, 48000},
    "loudness_lufs": -14.0,
    "bitrate_brackets": [
        # (max_height, min_bitrate, max_bitrate_h264, max_bitrate_hevc)
        (1080, 8_000_000, 12_000_000, 8_000_000),
        (1440, 16_000_000, 24_000_000, 16_000_000),
        (2160, 35_000_000, 68_000_000, 45_000_000),
    ],
}

def preflight(source: SourceMeta, plan: Plan,
              encoder: EncoderCandidate) -> list[PreflightFinding]: ...
```

Проверки:

| Code | Severity | Условие |
|---|---|---|
| `container.ok` | ok | container in YT_TARGETS |
| `fps.unusual` | warn | fps не в {24,25,30,50,60} ± 0.1 |
| `audio.sr.bad` | warn | sr != 48000 (YouTube ресемплит, но качество страдает) |
| `audio.multichannel.copy` | warn | source 5.1 + plan не обрабатывает — лишнее перекодирование под угрозой |
| `hdr.unsupported.encoder` | fail | source HDR + encoder не поддерживает 10-bit |
| `hdr.color.transforms` | fail | source HDR + есть color/eq transforms + `keep_hdr=false` |
| `subs.image_based` | warn | есть pgs/dvb — будут потеряны |
| `loudnorm.missing` | warn | profile без audio.loudnorm — выход не попадёт в -14 LUFS |
| `bitrate.over` | warn | предполагаемый bitrate выхода > YT max для resolution |
| `transforms.aggressive` | warn | сумма ожидаемого VMAF-падения > 8 (по эмпирическим коэффициентам) |

### `cli/cmd_preflight.py`

```python
@app.command("preflight")
def preflight_cmd(
    input: Path = typer.Argument(..., exists=True),
    profile: Path = typer.Option(..., "--profile"),
    encoder: str | None = typer.Option(None),
) -> None:
    """Print findings; exit code 0 if no fail, 1 if any fail."""
```

### Обновление `cli/cmd_run.py`

Поток run теперь такой:

```python
def run_cmd(...):
    source = probe(input)
    profile = load_profile(profile_path)
    encoder = pick_encoder(detect_encoders(), prefer=[encoder_override], codec=profile.target_codec)
    plan = Plan(source=source, profile=profile, encoder=encoder, plan_hash=...)

    findings = preflight(source, plan, encoder)
    if any(f.severity == "fail" for f in findings):
        print_findings(findings); raise typer.Exit(1)

    work_dir = work_dir_for(input)
    store = CheckpointStore(work_dir, plan)
    segments = store.init_or_resume(plan_segments(plan))

    # audio loudnorm measurement (один раз, кешируется)
    measurement = store.get_loudnorm() or measure_loudnorm(source.path)
    store.set_loudnorm(measurement)

    # video segments (resume-aware)
    for seg in store.pending():
        store.mark(seg.idx, "in_progress")
        out = process_video_segment(seg, plan, work_dir, measurement, on_event=progress_emit)
        store.mark(seg.idx, "done", out_path=out)

    # main audio (один раз, кешируется)
    main_audio = store.get_main_audio() or process_main_audio(plan, work_dir, on_event=...)
    store.set_main_audio(main_audio)

    # concat
    metadata_args = build_metadata_args(source, plan)
    concat_segments([s.out_path for s in store.all_segments()], main_audio, plan, output, metadata_args)

    if not keep_segments:
        cleanup(work_dir)
```

Прогресс CLI: глобальный bar = `(sum done.duration + current.out_time) / total_duration`. Rich `Progress` с 2 колонками (overall + current segment).

## Acceptance

```bash
# 1. Resume test
yt-uniq run sample_long.mp4 --profile ...medium.yaml --out /tmp/A.mp4 &
PID=$!
sleep 20            # успеет обработать 1-2 сегмента
kill -INT $PID
yt-uniq run sample_long.mp4 --profile ...medium.yaml --out /tmp/A.mp4
# Должен пропустить готовые сегменты и завершиться.

# Сравнение с непрерывным прогоном:
yt-uniq run sample_long.mp4 --profile ...medium.yaml --out /tmp/B.mp4 \
  --work-dir /tmp/clean_work
md5 /tmp/A.mp4 /tmp/B.mp4
# Должны совпадать (или почти — допустим mux-noise, но VMAF(A,B) > 99).

# 2. Preflight HDR
yt-uniq preflight hdr_movie.mp4 --profile ...medium.yaml
# выход: fail на hdr.color.transforms; exit code 1

# 3. Preflight ok
yt-uniq preflight sample.mp4 --profile ...soft.yaml
# exit code 0

# 4. Multi-track preserve at concat
yt-uniq run multi_audio.mp4 --profile ...soft.yaml --out /tmp/multi.mp4
ffprobe -v error -show_streams /tmp/multi.mp4 -of json | \
  jq '[.streams[] | {codec_type, codec_name, channels}]'
# Все исходные дорожки сохранены, главная audio = AAC 256k, остальные copy.
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_segmenter_keyframes.py` | mock keyframes [0, 5, 10, 15, 20] + target 8s → boundaries [0-10, 10-20] |
| Unit | `tests/unit/test_segmenter_keyframes.py` | один keyframe в начале → один сегмент со всем файлом |
| Unit | `tests/unit/test_checkpoint.py` | init_or_resume: новый state создаётся; existing с тем же plan_hash подхватывается; разный plan_hash инвалидирует |
| Unit | `tests/unit/test_checkpoint.py` | mark атомарен: симулируем kill между write tmp и rename, state валиден |
| Unit | `tests/unit/test_metadata.py` | resolve_title({stem}_{profile}, ...) подставляет; -map_metadata -1 всегда первым |
| Unit | `tests/unit/test_preflight.py` | каждый код × ok/warn/fail случай |
| Unit | `tests/unit/test_preflight.py` | HDR + color transforms + keep_hdr=False → hdr.color.transforms fail |
| Integration | `tests/integration/test_resume_kill.py` | реальный прогон, kill после первого сегмента, перезапуск, итоговый md5 совпадает с no-kill прогоном (±VMAF 99) |
| Integration | `tests/integration/test_concat_seams.py` | прогон 3-сегментный, измеряем SSIM в окне ±2 кадра вокруг швов — не отличается от SSIM в середине сегмента более чем на 0.005 |
| Integration | `tests/integration/test_multitrack_through_segmenter.py` | 2-аудио + soft sub → после сегментации+concat все дорожки на месте |
| Integration | `tests/integration/test_loudnorm_cache.py` | первый run делает measurement; второй run читает из state.json (mock ffmpeg-вызов проверяет не вызывался) |
| Smoke | `tests/smoke/test_preflight_smoke.py` | preflight на 2-сек ok-клипе → exit 0 |

## Risks

- **Швы concat визуально заметны.** Митигация: все сегменты с идентичными encoder-параметрами, границы строго на IDR. Тест `test_concat_seams.py` проверяет.
- **Concat audio rate-mismatch** между сегментами и main_audio: и те и другие должны быть 48kHz AAC. Pipeline это гарантирует.
- **Длительность main_audio != сумме видео-сегментов** при speed change: pitch_tempo меняет длительность. Решение: video.speed.rate (если включён) тоже применяется к видео-сегментам, length matches. Тест проверяет `duration_match`.
- **`-map_metadata -1` теряет language tags.** Решение: явно проставляем `-metadata:s:a:N language=...` из исходных streams.
- **Image-based subs (pgs/dvb)** — теряем при `-c:s copy` если ffmpeg не умеет копировать в mp4. Решение: preflight warn, pgs/dvb пропускаются в mp4 контейнере.
- **`-avoid_negative_ts make_zero` при stream copy** может сместить PTS — обязательно при сегментации.
- **MD5-эквивалентность kill/no-kill хрупка** из-за timestamp в metadata. Решение: `creation_time` берётся из `state.json` если он есть, иначе now() — обеспечивает детерминизм.

## Hand-off в Spec 04/05

После Phase 3:
- `cli/cmd_run.py` обрабатывает полноразмерные файлы с resume.
- `state.json` готов как источник истины о прогрессе — может использоваться GUI (Spec 05).
- `preflight()` готов как отдельная функция — может вызываться из GUI до старта.
- QA report (Spec 04) получает на вход пару `(input_path, output_path)` независимо от resume.
