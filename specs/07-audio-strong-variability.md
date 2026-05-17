# Spec 07 — Audio strong + variability

> **Phase 7 (v0.2)** · 2 дня · **No deps** (parallel-safe with 06, 08)

## Goal

Аудио-сторона реально сдвигает chromaprint-фингерпринт (Jaccard
input↔output < 0.4 на `cid_aware` профиле), оставаясь перцептивно чистой.
Каждый запуск одной и той же конфигурации даёт другой output (для batch
N-вариантов одного исходника).

## Scope

**In:**

- Расширение `audio.pitch_tempo`: больший диапазон + per-run randomization.
- Расширение `audio.eq`: jitter полос на каждом run.
- Новый `audio.resample` — микро-SR-trip размывает спектральные пики.
- Новый `audio.spectral_smear` — chorus/aphaser с минимальной интенсивностью.
- Новый `video.mirror` — hflip (опционально, не дефолт).
- `seed_strategy` в `Profile`: `fixed | per_run | per_file`.
- `run_seed` в `Plan`; перерасчёт `plan_hash` с учётом seed.
- Два новых профиля: `cid_aware.yaml` (рекомендуемый), `cid_aggressive.yaml`.

**Not in:** TTS dub / voice cloning / audio re-synthesis. Только filter-based
transformations.

## Modules

### `core/transforms/audio_pitch.py` — расширение

```python
class PitchTempoParams(BaseModel):
    # ↓ Default pitch bumped from 1.005 to 1.012 (was useless for chromaprint).
    pitch: float = Field(default=1.012, ge=0.5, le=2.0)
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int = Field(default=48000, ge=8000, le=192000)
    # ↓ NEW: per-run jitter window. 0.003 = ±0.3%.
    randomize_within: float = Field(default=0.0, ge=0.0, le=0.05)
```

Builder использует `Plan.run_seed` (см. ниже):

```python
def _build_pitch_tempo(params, alloc, in_lbl, *, rng: random.Random | None = None):
    pitch = params.pitch
    if params.randomize_within > 0 and rng is not None:
        pitch += rng.uniform(-params.randomize_within, params.randomize_within)
    # … rest unchanged
```

`TransformSpec.build` сигнатура расширяется (опциональный kw-only `rng`).
Старые transforms игнорируют его.

### `core/transforms/audio_eq.py` — расширение

```python
class AudioEqParams(BaseModel):
    bands: list[tuple[float, float]] = Field(
        default_factory=lambda: [(120.0, -0.6), (4500.0, 0.4)]
    )
    width_q: float = Field(default=1.0, ge=0.1, le=10.0)
    randomize_bands: bool = False   # NEW
```

При `randomize_bands=True` каждая полоса jitter'ится:
- freq × uniform(0.95, 1.05)
- gain += uniform(-0.2, 0.2)

### `core/transforms/audio_resample.py` — NEW

```python
class AudioResampleParams(BaseModel):
    intermediate_sr: int = Field(default=47999, ge=8000, le=192000)
    target_sr: int = Field(default=48000, ge=8000, le=192000)

# Filter: aresample={intermediate_sr},aresample={target_sr}
# Two-step rate-trip blurs the spectral content imperceptibly.
```

### `core/transforms/audio_spectral_smear.py` — NEW

```python
class SpectralSmearParams(BaseModel):
    intensity: float = Field(default=0.02, ge=0.0, le=0.10)

# Filter: chorus delays=5:decays={intensity}:speeds=0.3:depths={intensity}
# - At intensity=0.02 imperceptible on dialogue/music
# - At intensity=0.1 noticeable phaser-like artifact
```

⚠️ Default profile (`cid_aware`) НЕ включает spectral_smear. Только
`cid_aggressive` или явный opt-in.

### `core/transforms/video_geom.py` — добавление mirror

```python
class MirrorParams(BaseModel):
    """Horizontal flip. Destroys pHash similarity completely.

    WARNING: visible — text in frame becomes mirrored. Use only when
    content tolerates it (abstract / nature / B-roll, not narrative).
    """
    enabled: bool = True

# Registered as 'video.mirror'. Filter: hflip
```

### `core/models.py` — Plan.run_seed + Profile.seed_strategy

```python
SeedStrategy = Literal["fixed", "per_run", "per_file"]

class Profile(BaseModel):
    # … existing fields …
    seed_strategy: SeedStrategy = "per_run"   # NEW; default randomizes

class Plan(BaseModel):
    # … existing …
    run_seed: int   # NEW; randomized at build time per strategy
```

### `core/pipeline.py` — compute_plan_hash + run_seed plumbing

```python
def compute_plan_hash(source, profile, encoder, run_seed: int) -> str:
    payload = {
        # … existing …
        "seed_strategy": profile.seed_strategy,
        "run_seed": run_seed,
    }
    # …

def _resolve_run_seed(profile: Profile, source: SourceMeta) -> int:
    if profile.seed_strategy == "fixed":
        return profile.seed or 0
    if profile.seed_strategy == "per_file":
        # deterministic per-file
        return hash(str(source.path)) & 0xFFFFFFFF
    # per_run
    return random.randrange(2**32)
```

`FilterGraph` пробрасывает `random.Random(plan.run_seed)` в build()
каждого transform'а.

### `core/orchestrator.py` — `build_plan` обновляется

```python
def build_plan(input_path, profile, encoder_override):
    source = probe(input_path)
    enc = pick_encoder(detect_encoders(), prefer=…, codec=profile.target_codec)
    run_seed = _resolve_run_seed(profile, source)
    return Plan(
        source=source, profile=profile, encoder=enc,
        run_seed=run_seed,
        plan_hash=compute_plan_hash(source, profile, enc, run_seed),
    )
```

CLI добавляет `--new-variant` (force fresh seed даже при `seed_strategy=fixed`).

### `profiles/cid_aware.yaml`

```yaml
name: cid_aware
description: |
  Calibrated for Content ID divergence on owned/licensed content.
  Each run randomizes within safe bounds (variability for batch uploads).
transforms:
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
seed_strategy: per_run
target_codec: h264
target_loudness_lufs: -14.0
```

### `profiles/cid_aggressive.yaml`

Тот же базис + `video.speed=0.99`, `audio.spectral_smear (0.025)`,
`video.crop_resize.max_strength=0.07`. `video.mirror` остаётся `enabled: false`
по умолчанию.

## Acceptance

```bash
# 1. Two runs of cid_aware on same input produce DIFFERENT outputs.
yt-uniq run sample.mp4 --profile profiles/cid_aware.yaml --out v1.mp4
yt-uniq run sample.mp4 --profile profiles/cid_aware.yaml --out v2.mp4 --work-dir /tmp/w2
yt-uniq qa v1.mp4 v2.mp4 --no-vmaf
# Chromaprint Jaccard between v1 and v2 < 0.7

# 2. cid_aware reaches audio_fp Jaccard < 0.5 vs input.
yt-uniq qa sample.mp4 v1.mp4
# audio_fp_similarity < 0.5

# 3. VMAF still ≥ 88 on natural footage (synthetic clips lower, ok).
# (manual check on real clip)

# 4. cid_aggressive includes spectral_smear, mirror remains disabled.
grep spectral_smear profiles/cid_aggressive.yaml
# present
grep -A 2 video.mirror profiles/cid_aggressive.yaml
# enabled: false

# 5. seed_strategy=fixed gives reproducible output.
yt-uniq run sample.mp4 --profile profiles/cid_aware.yaml --out a.mp4 \
  --work-dir /tmp/a --override-seed-strategy fixed
yt-uniq run sample.mp4 --profile profiles/cid_aware.yaml --out b.mp4 \
  --work-dir /tmp/b --override-seed-strategy fixed
md5 a.mp4 b.mp4
# Equal (or within mux noise — chromaprint Jaccard ≈ 1.0)
```

(`--override-seed-strategy` — новый флаг cmd_run.)

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_transform_audio_resample.py` | snapshot фильтра + diff между intermediate_sr |
| Unit | `tests/unit/test_transform_audio_spectral_smear.py` | snapshot chorus при intensity 0.02 vs 0.1 |
| Unit | `tests/unit/test_transform_video_mirror.py` | hflip фильтр генерируется |
| Unit | `tests/unit/test_variability.py` | `_resolve_run_seed` per_run даёт случайные числа; per_file детерминирован; fixed = profile.seed |
| Unit | `tests/unit/test_plan_hash_with_seed.py` | разный `run_seed` → разный `plan_hash`; одинаковый — одинаковый |
| Unit | `tests/unit/test_pitch_randomize.py` | randomize_within с зафиксированным rng даёт ожидаемый jitter |
| Integration | `tests/integration/test_audio_fp_shift.py` | реальный 5-сек клип через cid_aware, chromaprint Jaccard input↔output < 0.6 (на synthetic ниже, but verify trend) |
| Integration | `tests/integration/test_two_runs_differ.py` | два прогона cid_aware → md5 разный, chromaprint Jaccard между output1/output2 < 0.85 |

## Risks

| Риск | Митигация |
|---|---|
| `per_run` ломает resume (новый seed → новый plan_hash → новый work_dir) | `state.json` сохраняет `run_seed` при первом запуске; resume игнорирует profile.seed_strategy если state существует |
| spectral_smear звучит неестественно на классической музыке/диалоге | Не в default-профиле; user opt-in через `cid_aggressive` или ручное добавление |
| mirror переворачивает буквы / lateralized content | `enabled: false` по умолчанию в cid_aggressive; пользователь включает явно |
| pitch 1.012 заметно (~21 cents) на музыке | Acceptable trade-off для контентов с диалогом; для музыкальных треков использовать `cid_aware_music.yaml` (отложено, v0.3) |
| Все existing snapshot-тесты pipeline_graph падают из-за new defaults | Обновить snapshot-фикстуры в test_pipeline_graph.py |

## Hand-off

После Phase 7:
- `cid_aware` — рекомендованный профиль для batch-загрузок.
- `Plan.run_seed` доступен всему ядру.
- Phase 8 (corpus + QA) проверяет именно outputs `cid_aware`.
- Phase 9 (calibration) масштабирует именно эти параметры — `scale_profile`
  знает, как `pitch.randomize_within` растёт пропорционально.
