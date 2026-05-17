"""Scale a Profile's transforms by a single `factor` knob.

`factor = 1.0` is the identity; >1 makes the profile more aggressive; <1 more
gentle. Each transform has its own per-param scaling rule:

  - around-zero values   (brightness): *= factor
  - around-one values    (contrast, gamma, saturation, pitch, tempo, speed.rate):
        x → 1 + (x - 1) * factor
  - magnitude knobs       (crop max_strength, rotate degrees,
                            randomize_within, smear intensity, noise strength,
                            eq band gain): *= factor
  - sample-rate delta     (audio.resample intermediate_sr around target):
        sr → target + (sr - target) * factor

Bounds: each pydantic schema enforces ge/le; we clamp to those before validation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.transforms import get


def scale_profile(profile: Profile, factor: float) -> Profile:
    """Return a copy of `profile` with all transform intensities scaled."""
    new_transforms: list[TransformConfig] = []
    for tc in profile.transforms:
        new_params = _scale_params(tc.id, dict(tc.params or {}), factor)
        new_transforms.append(
            TransformConfig(id=tc.id, enabled=tc.enabled, params=new_params)
        )
    return profile.model_copy(update={"transforms": new_transforms})


def _around_one(value: float, factor: float) -> float:
    return 1.0 + (value - 1.0) * factor


def _scale_params(
    transform_id: str, params: dict[str, Any], factor: float,
) -> dict[str, Any]:
    """Apply per-transform-id scaling; respect pydantic field bounds."""
    spec = get(transform_id)
    defaults = dict(spec.defaults)
    # Merge defaults so we operate on the effective starting values.
    effective = {**defaults, **params}

    scaled: dict[str, Any]
    if transform_id == "video.crop_resize":
        scaled = dict(effective)
        if "max_strength" in scaled:
            scaled["max_strength"] = scaled["max_strength"] * factor

    elif transform_id == "video.rotate":
        scaled = dict(effective)
        if "degrees" in scaled:
            scaled["degrees"] = scaled["degrees"] * factor

    elif transform_id == "video.color_eq":
        scaled = dict(effective)
        if "brightness" in scaled:
            scaled["brightness"] = scaled["brightness"] * factor
        for key in ("contrast", "gamma", "saturation"):
            if key in scaled:
                scaled[key] = _around_one(scaled[key], factor)

    elif transform_id == "video.noise":
        scaled = dict(effective)
        if "strength" in scaled:
            scaled["strength"] = int(round(scaled["strength"] * factor))

    elif transform_id == "video.speed":
        scaled = dict(effective)
        if "rate" in scaled:
            scaled["rate"] = _around_one(scaled["rate"], factor)

    elif transform_id == "video.blend_b":
        scaled = dict(effective)
        if "opacity" in scaled:
            scaled["opacity"] = scaled["opacity"] * factor

    elif transform_id == "video.mirror":
        scaled = dict(effective)  # nothing to scale

    elif transform_id == "audio.pitch_tempo":
        scaled = dict(effective)
        if "pitch" in scaled:
            scaled["pitch"] = _around_one(scaled["pitch"], factor)
        if "tempo" in scaled:
            scaled["tempo"] = _around_one(scaled["tempo"], factor)
        if "randomize_within" in scaled:
            scaled["randomize_within"] = scaled["randomize_within"] * factor

    elif transform_id == "audio.eq":
        scaled = dict(effective)
        if "bands" in scaled and scaled["bands"]:
            scaled["bands"] = [
                [float(freq), float(gain) * factor]
                for freq, gain in scaled["bands"]
            ]

    elif transform_id == "audio.resample":
        scaled = dict(effective)
        target = float(scaled.get("target_sr", 48000))
        inter = float(scaled.get("intermediate_sr", target))
        scaled["intermediate_sr"] = int(round(target + (inter - target) * factor))

    elif transform_id == "audio.spectral_smear":
        scaled = dict(effective)
        if "intensity" in scaled:
            scaled["intensity"] = scaled["intensity"] * factor

    elif transform_id == "audio.loudnorm":
        scaled = dict(effective)  # target loudness shouldn't scale with intensity

    else:  # unknown transforms pass through unchanged
        scaled = dict(effective)

    # Clamp to schema bounds (pydantic Field constraints).
    clamped = _clamp_to_schema(transform_id, scaled)
    # Strip defaults so we only emit the user's overrides in dumps.
    return {k: v for k, v in clamped.items() if defaults.get(k) != v or k in params}


def _clamp_to_schema(transform_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Cast numeric values into the field's [ge, le] range; ignore unknown fields."""
    spec = get(transform_id)
    out = deepcopy(params)
    for name, field in spec.schema.model_fields.items():
        if name not in out:
            continue
        value = out[name]
        meta = getattr(field, "metadata", []) or []
        ge_value: float | None = None
        le_value: float | None = None
        for m in meta:
            ge_value = ge_value if getattr(m, "ge", None) is None else m.ge
            le_value = le_value if getattr(m, "le", None) is None else m.le
        if isinstance(value, int | float):
            if ge_value is not None:
                value = max(value, ge_value)
            if le_value is not None:
                value = min(value, le_value)
            out[name] = value
    return out
