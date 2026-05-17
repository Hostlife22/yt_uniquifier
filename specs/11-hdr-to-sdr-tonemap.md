# Spec 11 — HDR → SDR Tonemap

> **Phase 11 (v0.3)** · 1.5 дня · **No deps** (parallel-safe with 12, 13)

## Goal

Один прогон конвертирует HDR-источник (PQ / HLG) в SDR-выход c осмысленным
маппингом яркости — без клипа highlights и без «крашится preflight'ом».
Пользователь, у которого HDR-мастер и SDR-аудитория, получает рабочий
SDR-вариант без ручного предварительного tonemap'инга через ffmpeg.

## Scope

**In:**

- Новый transform `video.tonemap_sdr` с алгоритмами `hable | reinhard |
  mobius | aces` и параметрами `peak / desat`.
- Расширение `core/transforms/hdr_wrap.py`: `to_sdr_chain()` —
  zscale-linear → tonemap → zscale-bt709 → yuv420p.
- `core/pipeline.py`: при наличии `video.tonemap_sdr` в profile пропускаем
  HDR-keep wrap (Phase 6) и форсируем `pix_fmt=yuv420p` независимо от
  `keep_hdr`.
- `core/preflight.py`: при `video.tonemap_sdr` подавляем
  `hdr.color.transforms` и `hdr.encoder.8bit` fails; добавляем
  `hdr.tonemap.ok` (severity=ok).
- Новый профиль `profiles/cid_aware_hdr_to_sdr.yaml`.
- QA: новый kwarg `vmaf.compute(..., hdr_aware: bool = False)` который при
  True подставляет `libvmaf=phone_model=0` (более снисходительная модель,
  ожидаемо нужна на HDR↔SDR парах).

**Not in:** Dolby Vision (нужен detect profile 5/7/8/8.1), HDR10+ dynamic
metadata, multi-target (HDR + SDR) одним прогоном.

## Modules

### `core/transforms/video_tonemap.py` (новый)

```python
from typing import Literal
from pydantic import BaseModel, Field

TonemapAlgo = Literal["hable", "reinhard", "mobius", "aces"]


class TonemapSDRParams(BaseModel):
    algorithm: TonemapAlgo = "hable"
    peak: float = Field(default=1000.0, ge=100.0, le=10000.0)
    desat: float = Field(default=0.0, ge=0.0, le=1.0)


def _build_tonemap_sdr(params, alloc, in_lbl, *, rng=None) -> FilterChain:
    out = alloc.next("v")
    # zscale linearise → tonemap → zscale BT.709 → 8-bit
    filt = (
        f"zscale=t=linear:npl={params.peak},"
        f"tonemap={params.algorithm}:desat={params.desat}:peak={params.peak/100:.3f},"
        "zscale=t=bt709:m=bt709:p=bt709:r=tv,"
        "format=yuv420p"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(TransformSpec(
    id="video.tonemap_sdr",
    kind="video",
    schema=TonemapSDRParams,
    build=_build_tonemap_sdr,
    defaults={"algorithm": "hable", "peak": 1000.0, "desat": 0.0},
))
```

### `core/transforms/hdr_wrap.py` — добавление helper

```python
def is_tonemap_active(profile_transforms: list[TransformConfig]) -> bool:
    """True if any enabled transform is video.tonemap_sdr."""
    return any(tc.enabled and tc.id == "video.tonemap_sdr"
               for tc in profile_transforms)
```

### `core/pipeline.py` — изменения

```python
from yt_uniquifier.core.transforms.hdr_wrap import is_tonemap_active

class FilterGraph:
    def build(self) -> BuiltCommand:
        # …
        tonemap = is_tonemap_active(self.plan.profile.transforms)

        hdr_wrap_enabled = (
            self.plan.profile.keep_hdr
            and bool(self.plan.source.video)
            and needs_linear_wrap(self.plan.source.video[0].color)
            and not tonemap                         # NEW: tonemap supersedes
        )
        # …

    def _target_pix_fmt(self) -> str:
        if not self.plan.source.video:
            return "yuv420p"
        v = self.plan.source.video[0]
        if is_tonemap_active(self.plan.profile.transforms):
            return "yuv420p"                        # NEW: tonemap → SDR
        if v.color.is_hdr and self.plan.profile.keep_hdr:
            return "yuv420p10le"
        return "yuv420p"
```

**Порядок transforms важен:** `video.tonemap_sdr` должен идти первым в
profile (иначе остальные color-transforms применяются в PQ-домене и дают
кривое цветоведение). Pipeline валидирует и эмитит preflight warn если
tonemap не первый.

### `core/preflight.py` — изменения

```python
def _check_hdr(source, plan, encoder) -> list[PreflightFinding]:
    # …
    tonemap = any(tc.enabled and tc.id == "video.tonemap_sdr"
                  for tc in plan.profile.transforms)
    if tonemap:
        findings.append(PreflightFinding(
            code="hdr.tonemap.ok", severity="ok",
            message=f"HDR source ({v.color.transfer}) will be tonemapped to BT.709 SDR.",
        ))
        # Tonemap supersedes both color-transform and encoder bit-depth fails.
        return findings
    # … existing logic for keep_hdr path …
```

Также добавляется warn если tonemap есть, но не первым:
```python
def _check_tonemap_order(plan):
    enabled = [tc for tc in plan.profile.transforms if tc.enabled]
    for i, tc in enumerate(enabled):
        if tc.id == "video.tonemap_sdr" and i != 0:
            return [PreflightFinding(
                code="tonemap.not_first", severity="warn",
                message="video.tonemap_sdr should be the first enabled transform "
                        "(otherwise color transforms apply in PQ domain).",
                suggestion="Move video.tonemap_sdr to the top of profile.transforms.",
            )]
    return []
```

### `core/qa/vmaf.py` — HDR-aware режим

```python
def compute(input_path, output_path, *, threads=4, subsample=1,
            hdr_aware: bool = False) -> VMAFResult:
    # …
    libvmaf_args = f"libvmaf=n_threads={threads}"
    if subsample > 1:
        libvmaf_args += f":n_subsample={subsample}"
    if hdr_aware:
        # libvmaf's phone_model is more lenient and tracks perceptual deltas
        # better on tonemapped HDR↔SDR pairs.
        libvmaf_args += ":phone_model=0"
    # …
```

### `profiles/cid_aware_hdr_to_sdr.yaml`

```yaml
name: cid_aware_hdr_to_sdr
description: |
  HDR (PQ / HLG) source → SDR output with CID-divergence transforms applied
  after tonemapping. Output is plain BT.709 yuv420p; any libx264-class
  encoder works.
transforms:
  - id: video.tonemap_sdr                  # MUST be first
    enabled: true
    params: {algorithm: hable, peak: 1000.0, desat: 0.0}
  - id: video.crop_resize
    enabled: true
    params: {max_strength: 0.04}
  - id: video.color_eq
    enabled: true
    params: {brightness: 0.015, contrast: 1.022, gamma: 0.99, saturation: 1.04}
  - id: video.noise
    enabled: true
    params: {strength: 5}
  - id: audio.pitch_tempo
    enabled: true
    params: {pitch: 1.012, randomize_within: 0.003}
  - id: audio.eq
    enabled: true
    params: {randomize_bands: true}
  - id: audio.resample
    enabled: true
    params: {intermediate_sr: 47999}
  - id: audio.loudnorm
    enabled: true
audio_tracks: first
keep_hdr: false
output_container: mp4
target_codec: h264
seed_strategy: per_run
```

## Acceptance

```bash
# HDR source through cid_aware_hdr_to_sdr profile.
yt-uniq run hdr_master.mkv \
  --profile src/yt_uniquifier/profiles/cid_aware_hdr_to_sdr.yaml \
  --out hdr_as_sdr.mp4 --encoder libx264

# Output is plain BT.709 SDR.
yt-uniq probe hdr_as_sdr.mp4 | jq '.video[0].color'
# {"is_hdr": false, "transfer": "bt709", "primaries": "bt709", ...}
yt-uniq probe hdr_as_sdr.mp4 | jq '.video[0].pix_fmt'
# "yuv420p"

# Preflight passes (no hdr.color.transforms / hdr.encoder.8bit fails).
yt-uniq preflight hdr_master.mkv \
  --profile src/yt_uniquifier/profiles/cid_aware_hdr_to_sdr.yaml
# OK   hdr.tonemap.ok: HDR source (smpte2084) will be tonemapped to BT.709 SDR.
# exit 0

# Reordered profile (tonemap second) → warn.
# preflight emits: WARN tonemap.not_first
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_transform_tonemap.py` | snapshot per algorithm; peak param влияет на npl и tonemap=peak |
| Unit | `tests/unit/test_pipeline_tonemap_path.py` | HDR source + tonemap → no zscale-roundtrip wrap; pix_fmt=yuv420p forced |
| Unit | `tests/unit/test_preflight_tonemap.py` | HDR + tonemap → no `hdr.color.transforms` fail; new `hdr.tonemap.ok`; tonemap-not-first → warn |
| Unit | `tests/unit/test_vmaf_hdr_aware.py` | mock subprocess: `hdr_aware=True` → `:phone_model=0` в lavfi |
| Integration | `tests/integration/test_hdr_to_sdr_roundtrip.py` | synthetic PQ clip → run with cid_aware_hdr_to_sdr → output is BT.709, VMAF (hdr_aware) ≥ 75 |

## Risks

| Риск | Митигация |
|---|---|
| `hable` пережимает highlights на dark cinema content | дать `--tonemap-algorithm aces` рекомендацию в docs/profiles.md |
| VMAF на HDR↔SDR паре даёт намного меньше чем HDR↔HDR | другой target (≥75 vs ≥88); явный note `vmaf measured tonemap-aware (phone_model=0)` в QA report |
| Tonemap+color_eq порядок неправильный → цвет ломается | preflight warn `tonemap.not_first`; в docs/profiles.md явный пример с правильным порядком |
| zscale нет в ffmpeg → tonemap не построится | preflight уже проверяет zscale availability (Phase 6 `hdr.zscale.missing`); тот же check переиспользуется для tonemap |
| Десaturate=0 даёт колорfully перенасыщенный SDR на брайтовых сценах | дефолт 0 (нейтральный); пользователь может задать 0.5 для cinematic look |

## Hand-off

После Phase 11:
- `video.tonemap_sdr` в registry; используется в любом профиле.
- HDR-видео можно гонять через `cid_aware_hdr_to_sdr.yaml` без ручных
  zscale-команд.
- `vmaf.compute(hdr_aware=True)` — публичный kwarg для CIDP_PREDICT и
  отчёта (можно добавить в orchestrator если detect HDR-source + SDR-output
  пары).
