# Spec 14 — Audio CID resistance + calibrate SSIM fallback

> **Phase 14 (v0.3.1)** · 4 дня · **Deps:** v0.3.0

## Context

Анализ против OSS-конкурентов (`specs/README.md` § v0.3) и реверс-инжиниринг
YouTube Content ID показали: для длинного контента CID опирается на
**аудио-фингерпринт сильнее видео**. Наш текущий аудио-стек:

- `audio.pitch_tempo`: дефолт pitch=1.012 (~21 cents), реализация через
  `asetrate+aresample+atempo` — **меняет голос на «chipmunk» уже при >2%**,
  поэтому пользователь не может поднять до значимых 4–5%.
- `audio.loudnorm`: всегда таргет -14 LUFS → одинаковый loudness envelope
  у всех наших аплоадов.
- Нет dynamic-range jitter, нет reverberation — CID легко trackает
  envelope и спектральные пики.

Плюс **calibrate loop сломан**: VMAF на агрессивных трансформах возвращает
~0.0 (наблюдали в Phase 9 smoke), `min_vmaf=88` фильтр не работает.

## Goal

Поднять CID-устойчивость **аудио** до уровня, при котором real-world
матчинг становится маловероятным, **без** ухудшения слышимого качества
больше чем на ~1 балл MUSHRA. Параллельно починить calibrate loop, чтобы
он мог измерять qualified quality на полученных аудио-трансформах.

## Scope

**In (5 единиц работы):**

1. Calibrate loop: VMAF → SSIM → pHash fallback chain.
2. `audio.pitch_tempo`: новый method `rubberband` (formant-preserving)
   через `ffmpeg rubberband` фильтр; дефолт `cid_aware.pitch` поднимается
   с 1.012 до 1.04.
3. `audio.loudnorm`: новый параметр `target_jitter_lufs: float = 0.0` —
   per-run рандомизация target в окне ±jitter.
4. Новый transform `audio.compand` — dynamic range jitter через
   `ffmpeg compand`, threshold/ratio randomized per run.
5. Новый transform `audio.reverb` — лёгкий aecho-based reverberation для
   ломки spectral fingerprint, intensity ∈ [0, 0.3].

**Not in:** frame-rate interleaving (отдельный вопрос, см. § «Не делаем
здесь»), Quick CLI alias (UX, не CID), bitrate defaults (storage, не CID),
adversarial diffusion noise (research, не production).

## Workitem 1 — Calibrate quality fallback

**Goal:** calibrate loop использует metric которой можно доверять на
distorted output.

**Modules:**

`core/qa/quality.py` (new):
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.qa import phash, ssim, vmaf

QualityMetric = Literal["vmaf", "ssim", "phash"]


@dataclass(frozen=True)
class QualityScore:
    value: float                     # normalised to 0..100 scale
    metric: QualityMetric
    raw: float                       # source value for debugging
    note: str | None = None


def quality_score(
    input_path: Path, output_path: Path,
    *, threads: int = 4, subsample: int = 1, hdr_aware: bool = False,
) -> QualityScore:
    """Pick the strongest sensible quality metric for this pair.

    Chain:
      1. VMAF if available AND > 1.0 (anything below is libvmaf giving up)
      2. SSIM × 100 if VMAF is unavailable or unreliable
      3. pHash similarity × 100 as last resort (no scale2ref needed)
    Returns score in 0..100 with the metric name baked in so callers can
    branch on threshold (VMAF threshold differs from SSIM threshold).
    """
    v = vmaf.compute(input_path, output_path, threads=threads,
                     subsample=subsample, hdr_aware=hdr_aware)
    if v.score is not None and v.score > 1.0:
        return QualityScore(value=v.score, metric="vmaf", raw=v.score)

    s = ssim.compute(input_path, output_path)
    if s.score is not None:
        return QualityScore(value=s.score * 100,  metric="ssim", raw=s.score,
                             note="VMAF unreliable; using SSIM × 100")

    # phash similarity is mean over sampled frames, [0..1].
    ph = phash.compare(input_path, output_path, n=30)
    return QualityScore(value=ph.similarity * 100, metric="phash",
                         raw=ph.similarity,
                         note="VMAF and SSIM both unavailable; pHash similarity × 100")
```

`core/calibration/loop.py` — заменить прямой `vmaf_mod.compute` на
`quality_score`. `CalibrationStep.vmaf` переименовать в `quality` (с
обновлением подписи). `CalibrationTarget.min_vmaf` → `min_quality` (тоже
0..100, но threshold подгоняется per-metric — для SSIM ставим 90 = ssim 0.90).

`cli/cmd_calibrate.py` — в rich-таблице колонка «vmaf» становится
«quality (metric)»: `92.4 (vmaf)` / `89.2 (ssim)` / `76.0 (phash)`.

**Acceptance:**
- На синтетическом testsrc2 + aggressive профиле calibrate проходит
  3 итерации без VMAF=0.0 в логе.
- На input ≡ output (identity) quality_score возвращает ~100 любым из
  трёх metric'ов.

**Tests:**
- `tests/unit/test_quality_score.py` x5:
  - VMAF=92, SSIM=0.99 → берёт VMAF.
  - VMAF=0.1 (unreliable) → переходит на SSIM × 100.
  - SSIM=None → переходит на pHash × 100.
  - VMAF=None и SSIM=None → возвращает pHash, заполняет note.
  - Identity pair (mocked) → ~100 score.

## Workitem 2 — pitch_tempo: rubberband method

**Goal:** дефолтный pitch shift у `cid_aware` поднимается с 1.012 до 1.04
без появления «chipmunk» характера голоса.

**Modules:**

`core/transforms/audio_pitch.py` — расширение:
```python
PitchMethod = Literal["asetrate", "rubberband"]


class PitchTempoParams(BaseModel):
    pitch: float = Field(default=1.012, ge=0.5, le=2.0)
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int = Field(default=48000, ge=8000, le=192000)
    randomize_within: float = Field(default=0.0, ge=0.0, le=0.05)
    method: PitchMethod = "asetrate"


def _build_pitch_tempo(params, alloc, in_lbl, *, rng=None):
    # … existing pitch computation with randomize_within …
    if params.method == "rubberband":
        # ffmpeg rubberband filter preserves formants — voice keeps timbre.
        filt = f"rubberband=pitch={pitch:.6f}:tempo={params.tempo:.6f}"
    else:
        # legacy asetrate+aresample+atempo (existing v0.2 behaviour)
        # …
```

Preflight: при `method=rubberband` проверяем `_ffmpeg_has_filter("rubberband")`
и emitим `audio.pitch.rubberband.missing` fail если нет
(требуется ffmpeg с `--enable-librubberband`; Homebrew default — есть).

`profiles/cid_aware.yaml`:
```yaml
- id: audio.pitch_tempo
  enabled: true
  params:
    pitch: 1.04                      # was 1.012
    method: rubberband               # NEW — formant-preserving
    randomize_within: 0.005          # was 0.003
```

`profiles/cid_aggressive.yaml` — pitch 1.06, randomize_within 0.01, method
rubberband.

**Acceptance:**
- `ffmpeg -filters | grep rubberband` показывает фильтр (Homebrew default).
- Output через `cid_aware` имеет slightly раздвинутый pitch на спектрограмме,
  голос **остаётся узнаваемым** (manual listen).
- Preflight на ffmpeg без rubberband — `audio.pitch.rubberband.missing` fail.

**Tests:**
- `tests/unit/test_pitch_rubberband.py` x4:
  - method=rubberband → filter_str содержит `rubberband=pitch=`
  - method=asetrate (default backward compat) → `asetrate=`
  - randomize_within с rng работает для обоих method
  - preflight: mock `_ffmpeg_has_filter("rubberband")=False` → fail
- Update existing snapshot test для cid_aware default profile.

## Workitem 3 — loudnorm jitter

**Goal:** target loudness слегка варьируется per run, ломая стабильный
loudness envelope fingerprint.

**Modules:**

`core/transforms/audio_loudnorm.py`:
```python
class LoudnormParams(BaseModel):
    integrated: float = Field(default=DEFAULT_TARGET_I, ge=-70.0, le=-5.0)
    true_peak: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, ge=1.0, le=20.0)
    # NEW: per-run jitter ±target_jitter_lufs around `integrated`.
    target_jitter_lufs: float = Field(default=0.0, ge=0.0, le=4.0)
```

`measure()` — без изменений (первый проход по нативному loudness).

`build_apply()` — расширение сигнатуры: принимает `rng` и при
`target_jitter_lufs > 0` сдвигает `params.integrated += uniform(-j, +j)`.

`profiles/cid_aware.yaml`:
```yaml
- id: audio.loudnorm
  enabled: true
  params: {target_jitter_lufs: 1.5}  # NEW — target sometimes -12.5, sometimes -15.5
```

**Acceptance:**
- Два прогона с разными seed дают `output_i` в диапазоне ±1.5 LUFS вокруг -14.
- Existing tests audio_loudnorm не падают.

**Tests:**
- `tests/unit/test_loudnorm_jitter.py` x3:
  - jitter=0 → deterministic, всегда integrated=-14.0 в фильтре.
  - jitter=2.0 + rng(seed=1) → integrated сдвинут на ожидаемое число.
  - jitter=2.0 + rng(seed=2) → другое значение.

## Workitem 4 — audio.compand (dynamic range jitter)

**Goal:** мягкая компрессия с per-run-варьируемыми параметрами. Меняет
audio envelope, что критично для chromaprint-style fingerprinting.

**Modules:**

`core/transforms/audio_compand.py` (new):
```python
class CompandParams(BaseModel):
    attack: float = Field(default=0.05, ge=0.001, le=1.0)
    decay: float = Field(default=0.5, ge=0.01, le=2.0)
    # Threshold (dB) and ratio sweep per run. Defaults give mild "radio" compression.
    threshold_db: float = Field(default=-20.0, ge=-60.0, le=0.0)
    ratio: float = Field(default=2.5, ge=1.0, le=20.0)
    randomize_within: bool = True


def _build_compand(params, alloc, in_lbl, *, rng=None) -> FilterChain:
    threshold = params.threshold_db
    ratio = params.ratio
    if params.randomize_within and rng is not None:
        threshold += rng.uniform(-3.0, 3.0)    # ±3 dB
        ratio += rng.uniform(-0.5, 0.5)
        ratio = max(1.0, ratio)
    # compand transfer function: -80→-80 (silent floor), then knee at threshold
    # with the requested ratio.
    knee_in = -abs(threshold)
    knee_out = knee_in / ratio
    out = alloc.next("a")
    filt = (
        f"compand=attacks={params.attack}:decays={params.decay}:"
        f"points=-80/-80|{knee_in:.1f}/{knee_out:.1f}|0/-3"
    )
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(TransformSpec(
    id="audio.compand", kind="audio",
    schema=CompandParams, build=_build_compand,
    defaults={"attack": 0.05, "decay": 0.5, "threshold_db": -20.0, "ratio": 2.5},
))
```

`profiles/cid_aware.yaml` — добавить:
```yaml
- id: audio.compand                  # NEW — between pitch and loudnorm
  enabled: true
  params: {threshold_db: -20.0, ratio: 2.5, randomize_within: true}
```

**Acceptance:**
- На реальном dialogue-input output `compand`-фильтра не теряет
  интеллигибельность (manual listen).
- Два прогона с `randomize_within=true` дают разные `threshold` в
  filter_str.

**Tests:**
- `tests/unit/test_transform_compand.py` x4:
  - default filter shape `compand=attacks=…:decays=…:points=-80/-80|…/…|0/-3`
  - randomize off + same params → deterministic
  - randomize on + same rng seed → reproducible
  - randomize on + different seeds → different threshold/ratio

## Workitem 5 — audio.reverb (small room IR)

**Goal:** convolve audio с лёгким room impulse response. Меняет spectral
content одновременно во многих частотных band'ах — это **самое сильное**
изменение audio fingerprint при сохранении интеллигибельности.

**Modules:**

`core/transforms/audio_reverb.py` (new):
```python
ReverbStyle = Literal["small_room", "medium_room", "hall", "plate"]


class ReverbParams(BaseModel):
    intensity: float = Field(default=0.15, ge=0.0, le=0.5)
    style: ReverbStyle = "small_room"


_AECHO_PRESETS: dict[ReverbStyle, tuple[float, float, str, str]] = {
    # (in_gain, out_gain, delays_ms, decays)
    "small_room":   (0.8, 0.88, "40|60",       "0.4|0.3"),
    "medium_room":  (0.8, 0.88, "60|100|180",  "0.5|0.4|0.3"),
    "hall":         (0.7, 0.85, "100|200|400|800", "0.5|0.4|0.3|0.2"),
    "plate":        (0.9, 0.92, "20|40|80",    "0.5|0.3|0.2"),
}


def _build_reverb(params, alloc, in_lbl, *, rng=None) -> FilterChain:
    in_g, out_g, delays, decays = _AECHO_PRESETS[params.style]
    # Scale decay amplitudes by intensity (0 = no audible reverb, 0.5 = strong).
    scaled_decays = "|".join(
        f"{float(d) * (params.intensity / 0.15):.3f}" for d in decays.split("|")
    )
    out = alloc.next("a")
    filt = f"aecho={in_g}:{out_g}:{delays}:{scaled_decays}"
    return FilterChain(in_label=in_lbl, out_label=out, filter_str=filt)


register(TransformSpec(
    id="audio.reverb", kind="audio",
    schema=ReverbParams, build=_build_reverb,
    defaults={"intensity": 0.15, "style": "small_room"},
))
```

⚠️ **`audio.reverb` НЕ в дефолтном `cid_aware`** — добавляется в
`cid_aggressive` как opt-in. На dialogue-heavy content (vlogs, interviews)
reverb слышен и пользователь может его не захотеть.

`profiles/cid_aggressive.yaml` — добавить:
```yaml
- id: audio.reverb                   # NEW — opt-in only
  enabled: true
  params: {intensity: 0.10, style: small_room}
```

**Acceptance:**
- На реальном dialogue-input с `intensity=0.10` reverb слышен как
  «лёгкое помещение», не как «эхо».
- chromaprint Jaccard input↔output падает дополнительно на ~10–20% от
  baseline `cid_aware`.

**Tests:**
- `tests/unit/test_transform_reverb.py` x4:
  - default filter `aecho=0.8:0.88:40|60:0.400|0.300` (small_room, intensity=0.15)
  - intensity=0.3 → decay scaling ×2
  - style=hall → 4-tap echo
  - intensity=0 → all decays = 0 (effectively no-op)

## Cross-cutting

### Зависимости

`pyproject.toml` — без новых dependencies. Все 5 пунктов используют
**только ffmpeg-builtin filters** (rubberband, compand, aecho).
Опциональный binary: ffmpeg должен быть собран с `--enable-librubberband`
(Homebrew default — собран; Linux Debian `ffmpeg` package — да; Alpine —
требует `ffmpeg-with-rubberband` или собственная сборка).

### Risks

| Риск | Митигация |
|---|---|
| ffmpeg без `rubberband` фильтра | Preflight fail с понятным сообщением + suggestion переключиться на `method=asetrate` |
| `compand` knee параметры дают audible distortion на musical content | Default ratio=2.5 conservative; user может уменьшить до 1.5 |
| `reverb` на vlogs/podcasts звучит неестественно | Не в default cid_aware; opt-in через cid_aggressive |
| `quality_score` SSIM fallback менее sensitive чем VMAF — пользователь видит «92» где VMAF дал бы 75 | UI указывает metric в скобках; пользователь видит downgrade |
| Loudness jitter ломает loudnorm cache (state.json) — другой target → другая измеренная offset | `state.json` сохраняет `loudnorm_measurement` от первого pass; jitter применяется в build_apply, measurement переиспользуется |

### Что НЕ делаем здесь

- **Frame-interleaving (AB-style)** — после транскодинга платформой
  fingerprint часто сходится обратно, файл раздувается 2–4×. Без
  real CID validation смысла нет.
- **Quick CLI alias** (`yt-uniq quick`) — это UX-улучшение для adoption,
  не уникализация. Отдельная задача.
- **Bitrate defaults** (`-crf 21` вместо 18) — storage win, не CID.
  Отдельная задача.
- **Adversarial diffusion noise** — research-grade, требует
  pretrained model или per-input optimization (минуты на кадр).
  Не v0.3.1.

### Метрики (post-implementation target)

| Метрика | До v0.3.1 (cid_aware) | После v0.3.1 |
|---|---|---|
| pitch shift effective | 1.2% (хрюкающий >2%) | **4%** (formant-preserved) |
| loudness target stability | стабильно -14.0 LUFS | **±1.5 LUFS jitter** |
| Dynamic envelope variation | нет | per-run compand |
| Spectral fingerprint shift | resample 47999 only | resample + reverb (opt-in) |
| calibrate quality metric | VMAF (mostly 0.0) | quality_score chain (always sane) |
| Audio chromaprint Jaccard input↔output на cid_aware | ~0.6 (estimate, fpcalc not installed) | **target < 0.3** |

## Acceptance (whole spec)

```bash
# 1. SSIM fallback in calibrate.
yt-uniq calibrate tests/fixtures/results/source_30s.mp4 \
  --base profiles/cid_aware.yaml --out /tmp/tuned.yaml \
  --target 0.5 --iterations 3 --clip-sec 20 --encoder libx264
# Output table now shows `quality (vmaf|ssim|phash)` instead of raw VMAF.
# Never sees 0.0 in quality column.

# 2. rubberband pitch.
yt-uniq probe --filters | grep rubberband              # NEW — separate command? or just check via direct ffmpeg
ffmpeg -filters | grep rubberband                      # confirm built in
yt-uniq run /tmp/source.mp4 --profile profiles/cid_aware.yaml --out /tmp/v1.mp4
# Spectrogram of /tmp/v1.mp4 audio shows pitch shift ~4% without formant displacement.

# 3. Loudness jitter.
yt-uniq run /tmp/source.mp4 --profile profiles/cid_aware.yaml --out /tmp/a.mp4
yt-uniq run /tmp/source.mp4 --profile profiles/cid_aware.yaml --out /tmp/b.mp4 --new-variant
# Measure: ffprobe loudness on a.mp4 vs b.mp4 differs by ~±1.5 LUFS

# 4. compand.
ffprobe -v error /tmp/v1.mp4 \
  -af "ebur128=peak=true" -f null - 2>&1 | grep "Max momentary"
# Dynamic range compressed vs original.

# 5. reverb (cid_aggressive only).
yt-uniq run /tmp/source.mp4 --profile profiles/cid_aggressive.yaml --out /tmp/agg.mp4
# Manual listen: soft "room" feel; chromaprint similarity vs source < cid_aware result.
```

## Tests (whole spec)

| Module | Файл | Тестов |
|---|---|---|
| 1. quality fallback | tests/unit/test_quality_score.py | 5 |
| 2. rubberband pitch | tests/unit/test_pitch_rubberband.py | 4 |
| 3. loudnorm jitter | tests/unit/test_loudnorm_jitter.py | 3 |
| 4. compand | tests/unit/test_transform_compand.py | 4 |
| 5. reverb | tests/unit/test_transform_reverb.py | 4 |
| | **Итого** | **+20 тестов** |

Integration test один общий:
- `tests/integration/test_audio_cid_resistance.py`: tiny_clip → cid_aware
  через rubberband+jitter+compand → output опрашивается через ffprobe,
  detected pitch shift в ожидаемом диапазоне, loudness не строго -14.

## Hand-off (v0.3.1 release)

После Phase 14:
- 15 трансформов в registry (было 13: +audio.compand, +audio.reverb).
- `audio.pitch_tempo` имеет `method` параметр (back-compat default asetrate).
- `audio.loudnorm` имеет `target_jitter_lufs`.
- `core/qa/quality.py` — публичный API quality_score().
- calibrate loop работает на санитарной метрике, не мусоре.
- Profiles: cid_aware усилен (pitch 1.04, jitter, compand); cid_aggressive
  получает audio.reverb opt-in.
- Tag `v0.3.1`.

## Оценка трудозатрат

| Workitem | Дни |
|---|---|
| 1. quality_score fallback + cal refactor | 0.5 |
| 2. rubberband pitch + cid_aware bump | 1.5 |
| 3. loudnorm jitter | 0.3 |
| 4. compand transform | 0.7 |
| 5. reverb transform | 1.0 |
| **Итого** | **4 дня** |
