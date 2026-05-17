"""Import each transform submodule so its register() runs at import time."""

from yt_uniquifier.core.transforms import (
    audio_eq,
    audio_loudnorm,
    audio_pitch,
    audio_resample,
    audio_spectral_smear,
    video_blend,
    video_color,
    video_geom,
    video_noise,
    video_speed,
    video_tonemap,
)
from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    all_ids,
    get,
    register,
)

__all__ = [
    "FilterChain",
    "LabelAllocator",
    "TransformSpec",
    "all_ids",
    "audio_eq",
    "audio_loudnorm",
    "audio_pitch",
    "audio_resample",
    "audio_spectral_smear",
    "get",
    "register",
    "video_blend",
    "video_color",
    "video_geom",
    "video_noise",
    "video_speed",
    "video_tonemap",
]
