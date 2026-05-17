"""Load a Profile from a YAML file with pydantic validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile


class ProfileLoadError(YtUniquifierError):
    """The YAML profile is missing or invalid."""


def load_profile(path: Path) -> Profile:
    if not path.exists():
        raise ProfileLoadError(f"profile file not found: {path}")
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileLoadError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileLoadError(f"profile root must be a mapping, got {type(raw).__name__}")
    try:
        return Profile.model_validate(raw)
    except Exception as exc:
        raise ProfileLoadError(f"profile validation failed for {path}: {exc}") from exc
