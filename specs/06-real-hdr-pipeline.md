# Spec 06 — Real HDR Pipeline

> **Phase 6 (v0.2)** · 1.5 дня · **No deps** (parallel-safe with 07, 08)

## Goal

HDR (PQ/HLG) видео реально проходит через `color_eq`, `noise`, `blend_b` без
потери цветового пространства, и остаётся HDR на выходе. v0.1 только
блокирует такое — `preflight=fail`. v0.2 должна разрешать.

## Scope

**In:**

- `core/transforms/hdr_wrap.py` — функции-обёртки `wrap_linear` / `wrap_pq` /
  `wrap_hlg`, оборачивающие участок цепочки фильтров в `zscale=transfer=linear`
  и обратно.
- `core/pipeline.py` — детектит HDR + `keep_hdr=True` и автоматически
  оборачивает блок color-touching transforms.
- `core/preflight.py` — для HDR + `keep_hdr` проверяет наличие `zimg`-фильтра
  и 10-bit-возможностей энкодера.
- `core/transforms/video_geom.py` — `rotate.fillcolor` адаптируется к HDR
  (черный в PQ-домене = 16, не 0).
- `profiles/medium_hdr.yaml` — рекомендуемый HDR-профиль.

**Not in:** HDR→SDR tonemap (v0.3), dolby vision (нужен профиль 5/7/8 detect,
не сейчас), HDR10+ dynamic metadata.

## Modules

### `core/transforms/hdr_wrap.py`

```python
from yt_uniquifier.core.models import HDRInfo
from yt_uniquifier.core.transforms.base import LabelAllocator

def needs_linear_wrap(color: HDRInfo) -> bool:
    """True if color transforms over this stream need zscale linear."""
    return color.is_hdr  # PQ or HLG

def wrap_linear(
    in_label: str,
    inner_filters: list[str],   # color transforms to apply in linear domain
    color: HDRInfo,
    alloc: LabelAllocator,
) -> tuple[str, str]:
    """
    Compose:
       [in] zscale=transfer=linear:npl=100,
            <inner_filters>,
            zscale=transfer=<orig> [out]
    Returns (out_label, filter_str).
    """

def npl_for(color: HDRInfo) -> int:
    """Nominal peak luminance: 100 for PQ-encoded grade, 1000 for true HDR10."""
```

Mapping back to the source transfer:

| Source transfer | Linear domain return | Wrap |
|---|---|---|
| `smpte2084` (PQ) | linear @ 10000 nits | `zscale=t=linear:npl=100` → ops → `zscale=t=smpte2084` |
| `arib-std-b67` (HLG) | linear @ 1000 nits | `zscale=t=linear:npl=100` → ops → `zscale=t=arib-std-b67` |
| `bt709` (SDR) | not wrapped | identity |

### `core/pipeline.py` — точка интеграции

В `FilterGraph.build()` после сборки видео-цепочки:

```python
COLOR_TRANSFORMS = {"video.color_eq", "video.noise", "video.blend_b"}

if plan.profile.keep_hdr and plan.source.video[0].color.is_hdr:
    # Группируем подряд идущие color-transforms в один wrap.
    grouped = group_consecutive(video_transforms, lambda tc: tc.id in COLOR_TRANSFORMS)
    for group in grouped:
        if group.is_color and len(group.items) > 0:
            # Заменяем участок цепочки на wrap_linear(...)
```

Геометрия (`crop_resize`, `rotate`, `speed`) HDR не ломает — пропускается
без обёртки.

### `core/preflight.py` — дополнительные checks

```python
def _check_hdr_keep_capability(
    source: SourceMeta, plan: Plan, encoder: EncoderCandidate
) -> list[PreflightFinding]:
    findings = []
    if not (source.video and source.video[0].color.is_hdr and plan.profile.keep_hdr):
        return findings
    if not _ffmpeg_has_filter("zscale"):
        findings.append(PreflightFinding(
            code="hdr.zscale.missing", severity="fail",
            message="ffmpeg lacks zscale (zimg) — required to keep HDR through transforms.",
            suggestion="Install ffmpeg built with --enable-libzimg.",
        ))
    if encoder.name == "libx264":
        findings.append(PreflightFinding(
            code="hdr.encoder.8bit", severity="fail",
            message="libx264 cannot output 10-bit HDR; choose libx265/hevc_nvenc/hevc_videotoolbox.",
        ))
    return findings
```

`_ffmpeg_has_filter` использует `ffmpeg -filters` (кеш как у
`vmaf_available`).

### `core/transforms/video_geom.py` — патч rotate

```python
class RotateParams(BaseModel):
    degrees: float = Field(default=0.15, ge=-2.0, le=2.0)
    fillcolor_pq: str = "#101010"   # near-black in PQ domain, not pure 0
    fillcolor_sdr: str = "black"
```

`pipeline` выбирает `fillcolor` по `plan.source.video[0].color.transfer`.

### `profiles/medium_hdr.yaml`

```yaml
name: medium_hdr
description: HDR (PQ/HLG) preserved through color transforms.
transforms:
  - id: video.crop_resize
    enabled: true
    params: {max_strength: 0.025, rng_seed: 42}
  - id: video.color_eq
    enabled: true
    params: {brightness: 0.008, contrast: 1.012, gamma: 0.997, saturation: 1.02}
  - id: video.noise
    enabled: true
    params: {strength: 3}
  # No rotate — HDR rotate fillcolor edges are tricky.
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.0008, tempo: 1.0}
  - id: audio.eq
    enabled: true
  - id: audio.loudnorm
    enabled: true
audio_tracks: first
keep_hdr: true
output_container: mp4
target_codec: hevc                # x264 cannot 10-bit
target_loudness_lufs: -14.0
seed: 42
```

## Acceptance

```bash
# Generate a fake HDR test clip (testsrc + PQ metadata).
ffmpeg -y -f lavfi -i testsrc2=s=1280x720:r=24:d=2 \
  -vf "format=yuv420p10le,zscale=t=smpte2084:p=bt2020:m=bt2020nc" \
  -c:v libx265 -x265-params "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10-opt=1" \
  -pix_fmt yuv420p10le hdr_test.mp4

# Probe — verify HDR.
yt-uniq probe hdr_test.mp4 | jq '.video[0].color'
# {"is_hdr": true, "transfer": "smpte2084", "primaries": "bt2020", ...}

# Preflight against medium (SDR) — must FAIL with hdr.color.transforms.
yt-uniq preflight hdr_test.mp4 --profile src/yt_uniquifier/profiles/medium.yaml
# exit 1, "hdr.color.transforms: ... use --keep-hdr"

# Preflight against medium_hdr — should pass when zscale + libx265 present.
yt-uniq preflight hdr_test.mp4 --profile src/yt_uniquifier/profiles/medium_hdr.yaml --encoder libx265
# exit 0, ok findings only

# Run.
yt-uniq run hdr_test.mp4 --profile src/yt_uniquifier/profiles/medium_hdr.yaml \
  --encoder libx265 --out hdr_out.mp4

# Verify HDR preserved on output.
yt-uniq probe hdr_out.mp4 | jq '.video[0].color'
# {"is_hdr": true, "transfer": "smpte2084", "primaries": "bt2020", ...}

# VMAF in HDR mode > 90.
yt-uniq qa hdr_test.mp4 hdr_out.mp4 --no-audio-fp
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_hdr_wrap.py` | `needs_linear_wrap` для PQ/HLG/SDR; snapshot строки wrap для PQ-color_eq |
| Unit | `tests/unit/test_pipeline_hdr_grouping.py` | consecutive color transforms собираются в один wrap; geometry между ними не оборачивается |
| Unit | `tests/unit/test_preflight_hdr_caps.py` | mock `_ffmpeg_has_filter` → no zscale → fail; libx264 + HDR → fail |
| Integration | `tests/integration/test_hdr_roundtrip.py` | реальный 2-сек HDR-клип через medium_hdr → output остаётся PQ, VMAF > 88 |

## Risks

- **`zscale` отсутствует в дефолтном macOS Homebrew ffmpeg** до недавнего
  времени. Если у пользователя нет — preflight даёт понятный fail с
  командой установки.
- **VMAF в HDR-режиме** требует `libvmaf=phone_model=0` и других флагов;
  скорее всего наш дефолтный VMAF call даст заниженный результат. v0.2
  оставляет это как known limitation; v0.3 — отдельная HDR-метрика.
- **Округление precision** в zscale linear↔PQ — каждый roundtrip теряет
  ~0.5 пункта VMAF. Допустимо для одного прохода.
- **`pix_fmt yuv420p10le`** требуется на выходе. Pipeline уже выбирает его
  для HDR (Phase 2), но `_target_pix_fmt` нужно проверить — для x265
  передаём явно.

## Hand-off

После Phase 6:
- HDR-источники не блокируются preflight'ом (когда есть keep_hdr).
- `medium_hdr.yaml` — готовый рецепт.
- Дальнейшие фазы (7, 8) могут не учитывать HDR — обёртки прозрачны.
