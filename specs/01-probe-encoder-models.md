# Spec 01 — Probe, Encoder Detect, Data Models

> **Phase 1** · 1-2 дня · **Deps:** [00-bootstrap](./00-bootstrap.md)

## Goal

`yt-uniq probe <input>` печатает JSON со всеми потоками, HDR-меткой, главами.
`yt-uniq probe --encoders` печатает список реально работающих аппаратных кодеков.
Pydantic-модели готовы как контракт для всех последующих фаз.

## Scope

**In:**

- `core/models.py` — все pydantic-классы, которыми обменивается ядро.
- `core/probe.py` — `probe(path) -> SourceMeta` через ffprobe (без OpenCV).
- `core/encoder.py` — `detect_encoders()` с реальным тест-запуском + кеш.
- `cli/cmd_probe.py` — typer-команда поверх обоих.

**Not in:** трансформации, pipeline, segmenter, QA.

## Modules

### `core/models.py`

Полный список pydantic v2 моделей. Все `frozen=True` где имеет смысл.

```python
from pathlib import Path
from typing import Literal, Sequence
from pydantic import BaseModel, Field, ConfigDict

ColorTransfer = Literal["bt709", "smpte2084", "arib-std-b67", "bt470bg", "smpte170m", "unknown"]
ColorPrimaries = Literal["bt709", "bt2020", "bt470bg", "smpte170m", "unknown"]
ColorSpace = Literal["bt709", "bt2020nc", "bt2020c", "bt470bg", "smpte170m", "unknown"]
StreamKind = Literal["video", "audio", "subtitle", "data"]
EncoderKind = Literal["h264", "hevc"]
EncoderVendor = Literal["nvenc", "qsv", "amf", "videotoolbox", "x264", "x265"]

class HDRInfo(BaseModel):
    is_hdr: bool
    transfer: ColorTransfer
    primaries: ColorPrimaries
    space: ColorSpace
    bit_depth: int

class VideoStream(BaseModel):
    index: int
    codec: str
    width: int
    height: int
    fps: float
    duration_sec: float
    pix_fmt: str
    bit_rate: int | None
    color: HDRInfo
    is_default: bool

class AudioStream(BaseModel):
    index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str | None
    bit_rate: int | None
    language: str | None
    is_default: bool

class SubtitleStream(BaseModel):
    index: int
    codec: str               # mov_text/srt/ass/pgs/dvb_subtitle/...
    language: str | None
    is_image_based: bool     # True для pgs/dvb_subtitle

class Chapter(BaseModel):
    start_sec: float
    end_sec: float
    title: str | None

class SourceMeta(BaseModel):
    path: Path
    container: str           # mp4/mkv/mov/...
    duration_sec: float
    size_bytes: int
    video: list[VideoStream]
    audio: list[AudioStream]
    subtitle: list[SubtitleStream]
    chapters: list[Chapter]

class EncoderCandidate(BaseModel):
    name: str                # h264_nvenc / libx264 / ...
    vendor: EncoderVendor
    codec: EncoderKind
    works: bool              # успешный тест-запуск
    error: str | None

class TransformConfig(BaseModel):
    id: str                  # "video.crop_resize"
    enabled: bool = True
    params: dict             # валидируется на стороне Transform.schema

class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str | None = None
    transforms: list[TransformConfig]
    audio_tracks: Literal["first", "all"] | list[int] = "first"
    keep_hdr: bool = False
    output_container: Literal["mp4", "mov"] = "mp4"
    target_codec: EncoderKind = "h264"
    target_loudness_lufs: float = -14.0
    seed: int | None = None  # для воспроизводимого random в transforms

class Plan(BaseModel):
    """Сериализуемый план обработки. Hash от него = ключ resume."""
    source: SourceMeta
    profile: Profile
    encoder: EncoderCandidate
    plan_hash: str           # sha256 от (profile, encoder.name, source.video[0].codec...)

class Segment(BaseModel):
    idx: int
    start_sec: float
    end_sec: float
    status: Literal["pending", "in_progress", "done", "failed"]
    src_path: Path | None    # промежуточный stream-copy сегмент
    out_path: Path | None    # обработанный сегмент

class QAReport(BaseModel):
    input_md5: str
    output_md5: str
    phash_min: int
    phash_mean: float
    phash_max: int
    audio_fp_similarity: float | None
    vmaf_mean: float | None
    ssim_mean: float | None
    duration_match: bool
    notes: list[str]
```

### `core/probe.py`

```python
from pathlib import Path
from yt_uniquifier.core.models import SourceMeta

def probe(path: Path) -> SourceMeta:
    """One ffprobe call -> SourceMeta. Raises ProbeError on bad input."""
```

Реализация: `ffprobe -v error -show_format -show_streams -show_chapters -of json <path>`. Парсит:

- `streams[*].codec_type` → разводит по video/audio/subtitle.
- `streams[*].codec_tag_string`, `color_primaries`, `color_transfer`, `color_space`, `bits_per_raw_sample` → `HDRInfo`. `is_hdr = transfer in ("smpte2084", "arib-std-b67")`.
- `streams[*].tags.language` → AudioStream.language.
- `chapters[*].{start_time, end_time, tags.title}` → Chapter.
- `format.duration` и `format.size`.
- `r_frame_rate` парсится из `num/den` (как в старом `src/main.py:142-145`).
- Image-based subs: `codec_name in {pgs, hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle}` → `is_image_based=True`.

Без cv2-fallback. Если ffprobe ничего не вернул — `raise ProbeError(...)`.

### `core/encoder.py`

```python
from pathlib import Path
from yt_uniquifier.core.models import EncoderCandidate, EncoderKind

CACHE_PATH = Path.home() / ".cache" / "yt_uniquifier" / "encoders.json"
CACHE_TTL_SEC = 7 * 24 * 3600

def detect_encoders(force: bool = False) -> list[EncoderCandidate]:
    """
    Probe each candidate encoder with a real `ffmpeg -f lavfi -i nullsrc... -c:v <enc> -f null -`.
    Results cached at CACHE_PATH keyed by (ffmpeg --version output hash).
    """

def pick_encoder(
    candidates: list[EncoderCandidate],
    *,
    prefer: Sequence[str] | None = None,
    codec: EncoderKind = "h264",
) -> EncoderCandidate:
    """Pick highest-priority working encoder matching codec, with libx264/libx265 fallback."""
```

Кандидаты (порядок приоритета):

```
h264: h264_nvenc, h264_qsv, h264_videotoolbox, h264_amf, libx264
hevc: hevc_nvenc, hevc_qsv, hevc_videotoolbox, hevc_amf, libx265
```

Тест-команда (как у `ishuvl/video-dedup-tool`):

```
ffmpeg -hide_banner -f lavfi -i nullsrc=s=256x256:d=0.1 -c:v <enc> -f null -
```

Returncode == 0 → `works=True`. Иначе `works=False`, stderr-хвост → `error`.

### `cli/cmd_probe.py`

```python
import typer, json
from pathlib import Path
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.encoder import detect_encoders

@app.command("probe")
def probe_cmd(
    path: Path = typer.Argument(None, exists=True, dir_okay=False, readable=True),
    encoders: bool = typer.Option(False, "--encoders", help="List available encoders"),
    force_refresh: bool = typer.Option(False, "--refresh", help="Bypass encoder cache"),
) -> None:
    if encoders:
        out = [e.model_dump() for e in detect_encoders(force=force_refresh)]
    else:
        if not path:
            raise typer.BadParameter("path required unless --encoders")
        out = probe(path).model_dump(mode="json")
    typer.echo(json.dumps(out, indent=2, default=str))
```

Регистрация в `cli/app.py`:

```python
from yt_uniquifier.cli.cmd_probe import probe_cmd  # noqa: F401
```

(или через `app.add_typer` — выбрать одно стилистически).

## Acceptance

```bash
# 1. Probe реального файла
yt-uniq probe ~/movies/sample.mp4 | jq '.video[0]'
# выход: {"index":0,"codec":"h264","width":1920,"height":1080,"fps":23.976,...}

# 2. Probe HDR-файла
yt-uniq probe ~/movies/hdr_sample.mp4 | jq '.video[0].color.is_hdr'
# выход: true

# 3. Encoder detect
yt-uniq probe --encoders | jq '[.[] | select(.works)] | map(.name)'
# выход на macOS: ["h264_videotoolbox","hevc_videotoolbox","libx264","libx265"]
# выход на Linux+NVIDIA: ["h264_nvenc","hevc_nvenc","libx264","libx265"]

# 4. Кеш
yt-uniq probe --encoders         # первый раз — ~5 сек
yt-uniq probe --encoders         # второй — <100мс
yt-uniq probe --encoders --refresh   # снова медленно
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_probe.py` | парсит фикстурный ffprobe-JSON (захардкоженный в тесте) → проверяет все поля SourceMeta |
| Unit | `tests/unit/test_probe.py` | HDR detection: PQ → `is_hdr=True`, HLG → `is_hdr=True`, BT.709 → `False` |
| Unit | `tests/unit/test_probe.py` | image-based subs detection (pgs/dvb) |
| Unit | `tests/unit/test_encoder_detect.py` | mock `subprocess.run`, проверяет приоритет и fallback |
| Unit | `tests/unit/test_encoder_detect.py` | кеш: первый вызов пишет, второй читает; `--refresh` инвалидирует |
| Unit | `tests/unit/test_encoder_detect.py` | failed test-run → `works=False`, error заполнен |
| Unit | `tests/unit/test_models.py` | сериализация/десериализация Plan, plan_hash детерминирован |
| Integration | `tests/integration/test_probe_real.py` | `@pytest.mark.integration` — генерим короткий клип через ffmpeg testsrc2+sine, probe возвращает корректный SourceMeta |

Фикстуры:

- `tests/conftest.py` — fixture `tiny_clip(tmp_path)` генерит 2-сек 320x180 24fps testsrc2 + sine 440Hz через `ffmpeg -f lavfi -i testsrc2=...,sine=... -c:v libx264 -t 2 out.mp4`.

## Risks

- `ffprobe` color fields могут отсутствовать у старых файлов — все поля Optional/`"unknown"` с default'ами.
- На macOS GitHub runner videotoolbox может тестово работать, но реально не рендерить — пометить как `works=True` приемлемо, fallback на libx264 у пользователя если что.
- Размер кеша мизерный (<5KB), но atomic write через write-then-rename обязателен.

## Hand-off в Spec 02

После Phase 1:
- `SourceMeta`, `EncoderCandidate`, `Profile`, `Plan` готовы как контракт.
- `probe()` + `detect_encoders()` + `pick_encoder()` доступны для `pipeline.build()`.
- Профили (`profiles/*.yaml`) ещё не существуют — создаются в Spec 02.
