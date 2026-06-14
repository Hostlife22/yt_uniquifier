"""Transform package loader.

Each built-in transform submodule registers itself via ``register(...)`` at
import time. Importing this package eagerly imports every built-in so the
registry is populated by the time CLI/GUI code asks for ``get(id)``.

v0.8.0 R1 — plugin system:
  * Each built-in import is guarded so a single broken submodule (typically
    introduced by a developer mid-refactor or by an incompatible monkey-patch
    in a downstream environment) cannot prevent the rest of the tool from
    loading. The failure is logged at WARNING level — silent skip would mask
    a real packaging bug.
  * Third-party transforms can ship via the ``yt_uniquifier.transforms``
    entry-points group (see ``docs/plugins.md``). They are discovered after
    the built-ins so a plugin that imports a built-in transform sees it
    already in the registry.
"""

from __future__ import annotations

import importlib
import logging
from importlib import metadata as importlib_metadata

from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    all_ids,
    get,
    register,
)

_log = logging.getLogger(__name__)

# Ordered tuple so failures are reproducible across runs (regression debugging
# is much easier when "the third one fails" means the same thing every time).
_BUILTIN_TRANSFORMS: tuple[str, ...] = (
    "audio_compand",
    "audio_eq",
    "audio_haas",
    "audio_loudnorm",
    "audio_noise_overlay",
    "audio_pitch",
    "audio_resample",
    "audio_reverb",
    "audio_spectral_smear",
    "hdr_wrap",
    "video_blend",
    "video_color",
    "video_fit_aspect",
    "video_geom",
    "video_noise",
    "video_speed",
    "video_subpixel_sharpen",
    "video_subtitles",
    "video_temporal_jitter",
    "video_tonemap",
)

ENTRY_POINT_GROUP = "yt_uniquifier.transforms"


def _load_builtins() -> None:
    for name in _BUILTIN_TRANSFORMS:
        try:
            importlib.import_module(f"yt_uniquifier.core.transforms.{name}")
        except Exception as exc:  # noqa: BLE001 — explicit catch-all + log, never silent
            _log.warning("built-in transform %r failed to load: %s", name, exc)


def _discover_third_party() -> None:
    """Import every module advertised under the ``yt_uniquifier.transforms``
    entry-points group.

    A plugin module is expected to call ``register(TransformSpec(...))`` at
    import time, mirroring how built-ins work. The plugin's entry-point
    ``value`` is the import path (e.g. ``mypkg.my_transform``); the
    ``name`` is informational and surfaces only in WARNING logs.

    A broken plugin is logged and skipped — it must not prevent successful
    plugins or built-ins from loading.
    """
    try:
        entries = importlib_metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 — defensive against custom metadata backends
        _log.warning("entry-points lookup failed for %r: %s", ENTRY_POINT_GROUP, exc)
        return
    for ep in entries:
        try:
            ep.load()
        except Exception as exc:  # noqa: BLE001 — third-party code is untrusted-by-default
            _log.warning(
                "third-party transform plugin %r (%s) failed to load: %s",
                ep.name,
                ep.value,
                exc,
            )


_load_builtins()
_discover_third_party()

__all__ = [
    "ENTRY_POINT_GROUP",
    "FilterChain",
    "LabelAllocator",
    "TransformSpec",
    "all_ids",
    "get",
    "register",
]
