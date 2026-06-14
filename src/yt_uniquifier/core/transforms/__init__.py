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

from yt_uniquifier.core.plugin_sandbox import in_plugin_code, install_sandbox
from yt_uniquifier.core.plugins import (
    PluginViolation,
    list_entry_points,
    read_manifest,
    reset_active_manifest,
    set_active_manifest,
)
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

    v1.2.0 Task 23 — every plugin distribution must ship a
    ``yt_uniquifier_plugin.toml`` manifest declaring its capabilities.
    The manifest is read first (a missing or malformed manifest causes
    the plugin to be skipped with a WARN); the entry point is then
    imported with the manifest installed as the active context, so
    ``register()`` can enforce capability gating.  The audit-hook
    sandbox is active for the duration of plugin import + every plugin
    ``build()`` call so denylisted syscalls (filesystem mutation,
    network egress, subprocess spawn) raise ``PluginViolation`` instead
    of silently succeeding.

    Environment-variable overrides handled by ``list_entry_points``:

      * ``YT_UNIQ_NO_PLUGINS=1`` skips discovery entirely (used by the
        CLI's ``--no-plugins`` flag).
      * ``YT_UNIQ_PLUGINS_ALLOWLIST=a,b,c`` filters to named plugins.

    A broken plugin is logged and skipped — it must not prevent successful
    plugins or built-ins from loading.
    """
    # Installing the sandbox is idempotent and cheap; we do it eagerly
    # so even the manifest-read path (which doesn't import plugin code
    # directly but does call into TOML and pydantic) runs under the
    # same gate as the eventual plugin import.
    install_sandbox()
    for ep in list_entry_points(ENTRY_POINT_GROUP):
        try:
            manifest = read_manifest(ep)
        except PluginViolation as exc:
            _log.warning(
                "third-party transform plugin %r (%s) rejected at manifest: %s",
                ep.name, ep.value, exc,
            )
            continue
        token = set_active_manifest(manifest)
        try:
            with in_plugin_code():
                ep.load()
        except PluginViolation as exc:
            # Capability mismatch or sandbox catch — log explicitly so
            # operators see WHICH plugin and WHY (the WARN above for
            # missing manifest is distinct).
            _log.warning(
                "third-party transform plugin %r (%s) rejected at load: %s",
                ep.name, ep.value, exc,
            )
        except Exception as exc:  # noqa: BLE001 — third-party code is untrusted-by-default
            _log.warning(
                "third-party transform plugin %r (%s) failed to load: %s",
                ep.name, ep.value, exc,
            )
        finally:
            reset_active_manifest(token)


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
