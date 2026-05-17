"""Locate ffmpeg / ffprobe binaries.

Resolution order:
1. Environment variables YT_UNIQ_FFMPEG / YT_UNIQ_FFPROBE.
2. shutil.which() against PATH.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache

from yt_uniquifier.core.errors import FfmpegNotFoundError


def _find(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    path = shutil.which(name)
    if not path:
        raise FfmpegNotFoundError(
            f"{name!r} not found on PATH and {env_var} is not set. "
            "Install ffmpeg (https://ffmpeg.org/) and try again."
        )
    return path


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    return _find("ffmpeg", "YT_UNIQ_FFMPEG")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    return _find("ffprobe", "YT_UNIQ_FFPROBE")
