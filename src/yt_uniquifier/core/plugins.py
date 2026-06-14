"""v1.2.0 Task 23 — third-party plugin manifest + capability gate.

Built-in transforms self-register at import time (see
``core/transforms/__init__.py``).  v0.8.0 added discovery of third-party
transforms via the ``yt_uniquifier.transforms`` entry-points group.

v1.2.0 hardens that path:

  * Every third-party plugin distribution MUST ship a manifest file named
    ``yt_uniquifier_plugin.toml`` at the distribution root, declaring its
    name, version, and **capabilities** (``video_transform`` and/or
    ``audio_transform``).
  * During plugin load, ``register()`` is gated by the active manifest:
    a plugin without a manifest is rejected, and a registration whose
    transform kind isn't in the manifest's capabilities raises
    ``PluginViolation``.
  * The runtime sandbox (see ``plugin_sandbox.py``) installs a
    ``sys.addaudithook`` that blocks filesystem writes, network sockets,
    and subprocess spawns while plugin code is on the stack.
  * Operators can disable plugin loading entirely with ``--no-plugins``
    or restrict the set with ``--plugins-allowlist a,b``.

The manifest's optional ``sha256`` field is a self-declared integrity
hash of the package wheel; we record it for future supply-chain auditing
(Task 25 marketplace pins use the same shape) but don't recompute it at
load time today.
"""

from __future__ import annotations

import contextvars
import logging
import os
import tomllib
from importlib import metadata as importlib_metadata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yt_uniquifier.core.errors import PipelineError

_log = logging.getLogger(__name__)

MANIFEST_FILENAME = "yt_uniquifier_plugin.toml"

Capability = Literal["video_transform", "audio_transform"]
TransformKind = Literal["video", "audio"]


class PluginViolation(PipelineError):
    """Raised when a plugin tries to do something its manifest forbids,
    or when the sandbox catches a denylisted syscall (open-for-write,
    socket, subprocess) while plugin code is on the stack.
    """


class PluginManifest(BaseModel):
    """Pydantic model of ``yt_uniquifier_plugin.toml``.

    The ``capabilities`` field maps onto the ``TransformSpec.kind`` Literal
    via :func:`_kind_to_capability`.  Unknown top-level keys are rejected
    so a typo in a plugin's manifest fails loudly instead of silently
    granting unintended capabilities.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capabilities: tuple[Capability, ...] = Field(default_factory=tuple)
    # Optional self-declared wheel hash. Recorded for audit; not verified
    # here today (Task 25 marketplace pins use the same shape and DO
    # verify).
    sha256: str | None = None


# The active manifest is set BEFORE we trigger a plugin's import and
# cleared after.  ``register()`` reads this contextvar to decide whether
# the call is coming from a plugin (manifest set → enforce capabilities)
# or a built-in (manifest unset → unrestricted).  Using a contextvar
# rather than a module-level global keeps the gate thread-safe; the
# orchestrator and the GUI worker threads can each be loading plugins
# concurrently without trampling each other's state.
_ACTIVE_MANIFEST: contextvars.ContextVar[PluginManifest | None] = (
    contextvars.ContextVar("_yt_uniq_active_plugin_manifest", default=None)
)

# Plugin-name → set of spec.ids registered by that plugin.  Populated by
# ``transforms.base.register`` during plugin discovery; consulted by
# :func:`drop_disabled_plugins` so the CLI ``--no-plugins`` /
# ``--plugins-allowlist`` flags can post-filter the registry even when
# discovery ran at import time before the flags were parsed.
_PLUGIN_NAME_TO_SPEC_IDS: dict[str, set[str]] = {}


def record_plugin_spec(plugin_name: str, spec_id: str) -> None:
    _PLUGIN_NAME_TO_SPEC_IDS.setdefault(plugin_name, set()).add(spec_id)


def get_plugin_spec_ids(plugin_name: str) -> frozenset[str]:
    return frozenset(_PLUGIN_NAME_TO_SPEC_IDS.get(plugin_name, ()))


def all_plugin_names() -> tuple[str, ...]:
    return tuple(sorted(_PLUGIN_NAME_TO_SPEC_IDS))


def drop_disabled_plugins(
    *,
    no_plugins: bool = False,
    allowlist: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Post-filter the transform registry per CLI flags.

    Returns the tuple of dropped plugin names so the caller can log them.

    Pre-import filtering via ``YT_UNIQ_NO_PLUGINS`` /
    ``YT_UNIQ_PLUGINS_ALLOWLIST`` is the cleanest way to skip plugin
    loading altogether — but the corresponding CLI flags are parsed
    only after Typer has imported the command modules, which already
    transitively imported ``transforms``.  This helper bridges that
    gap by walking the registry and removing spec ids attributed to
    plugins the operator no longer wants active.

    ``no_plugins=True`` drops every plugin spec regardless of allowlist.
    ``allowlist`` (when not None) keeps only plugins whose ``name``
    matches; an empty frozenset drops all plugins.
    """
    # Local import to dodge the transforms ↔ plugins import cycle.
    from yt_uniquifier.core.transforms import base as transform_base

    dropped: list[str] = []
    for plugin_name in list(_PLUGIN_NAME_TO_SPEC_IDS):
        keep = (not no_plugins) and (
            allowlist is None or plugin_name in allowlist
        )
        if keep:
            continue
        for spec_id in _PLUGIN_NAME_TO_SPEC_IDS[plugin_name]:
            transform_base._REGISTRY.pop(spec_id, None)
            transform_base._PLUGIN_SPEC_IDS.discard(spec_id)
        _PLUGIN_NAME_TO_SPEC_IDS.pop(plugin_name)
        dropped.append(plugin_name)
    return tuple(dropped)


def get_active_manifest() -> PluginManifest | None:
    """Return the plugin manifest currently being loaded, if any."""
    return _ACTIVE_MANIFEST.get()


def set_active_manifest(
    manifest: PluginManifest | None,
) -> contextvars.Token[PluginManifest | None]:
    """Set the active manifest for the calling task/thread.

    Returns a contextvar Token that the caller must pass back to
    :func:`reset_active_manifest` to restore the previous value.  Using
    Token-based scoping rather than a try/finally with a literal value
    preserves contextvar semantics correctly under async / threading.
    """
    return _ACTIVE_MANIFEST.set(manifest)


def reset_active_manifest(
    token: contextvars.Token[PluginManifest | None],
) -> None:
    _ACTIVE_MANIFEST.reset(token)


def _kind_to_capability(kind: TransformKind) -> Capability:
    return "video_transform" if kind == "video" else "audio_transform"


def assert_kind_allowed(kind: TransformKind) -> None:
    """Verify the active manifest (if any) permits this transform kind.

    Built-in registrations (no active manifest) always pass.  A plugin
    that declared ``capabilities = ["video_transform"]`` and tries to
    register an audio transform raises :class:`PluginViolation`.
    """
    manifest = _ACTIVE_MANIFEST.get()
    if manifest is None:
        return  # built-in or test-only registration
    required = _kind_to_capability(kind)
    if required not in manifest.capabilities:
        raise PluginViolation(
            f"plugin {manifest.name!r} cannot register a {kind!r} transform: "
            f"its manifest declares capabilities {list(manifest.capabilities)!r}, "
            f"missing {required!r}.  Update yt_uniquifier_plugin.toml to opt in."
        )


def read_manifest(entry_point: importlib_metadata.EntryPoint) -> PluginManifest:
    """Locate and parse the manifest for an entry point's distribution.

    Searches the distribution's file list for ``yt_uniquifier_plugin.toml``.
    Raises :class:`PluginViolation` when:

      * the distribution metadata is unavailable (rare — usually means
        the entry point was registered by a hand-rolled metadata backend
        that doesn't expose the file list),
      * no manifest file is present,
      * the manifest fails to parse as TOML,
      * the manifest body fails pydantic validation.

    A manifest with empty ``capabilities`` is permitted at load time
    (the plugin is a no-op that registers nothing) but any subsequent
    ``register()`` call from that plugin will fail
    :func:`assert_kind_allowed`.
    """
    dist = entry_point.dist
    if dist is None:
        raise PluginViolation(
            f"entry point {entry_point.name!r} has no associated distribution; "
            "yt-uniquifier requires plugins to be installed packages so a "
            "manifest can be located."
        )
    files = dist.files or ()
    manifest_path = next(
        (f for f in files if f.name == MANIFEST_FILENAME),
        None,
    )
    if manifest_path is None:
        raise PluginViolation(
            f"plugin distribution {dist.metadata['Name']!r} (entry point "
            f"{entry_point.name!r}) is missing {MANIFEST_FILENAME!r}.  "
            "Every third-party transform plugin must ship this manifest "
            "at the package root; see docs/plugins.md § Manifest."
        )
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginViolation(
            f"failed to read {MANIFEST_FILENAME} for plugin {entry_point.name!r}: {exc}"
        ) from exc
    try:
        body = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise PluginViolation(
            f"plugin {entry_point.name!r}: {MANIFEST_FILENAME} is not valid TOML: {exc}"
        ) from exc
    # The shipped shape is `[plugin]` table with the fields at the
    # top level of that table.  Accept both `[plugin]` (preferred) and
    # the bare-top-level form, matching how setuptools handles
    # pyproject.toml's `[project]` table.
    payload = body.get("plugin", body)
    try:
        return PluginManifest.model_validate(payload)
    except ValidationError as exc:
        raise PluginViolation(
            f"plugin {entry_point.name!r}: {MANIFEST_FILENAME} failed validation: {exc}"
        ) from exc


def list_entry_points(
    group: str,
) -> tuple[importlib_metadata.EntryPoint, ...]:
    """Return entry points in ``group`` honouring the env-var overrides.

    ``YT_UNIQ_NO_PLUGINS=1`` short-circuits to an empty tuple so the
    CLI ``--no-plugins`` flag (which sets the env var before import)
    is effective even when the discovery is triggered from
    ``transforms/__init__.py`` at module import time.

    ``YT_UNIQ_PLUGINS_ALLOWLIST=a,b,c`` filters the returned tuple to
    entry points whose ``name`` matches one of the comma-separated
    allowlist values.  An empty / missing variable leaves the set
    unfiltered.
    """
    if os.environ.get("YT_UNIQ_NO_PLUGINS") == "1":
        return ()
    try:
        eps = importlib_metadata.entry_points(group=group)
    except Exception as exc:  # noqa: BLE001 — third-party metadata backends vary
        _log.warning("entry-points lookup failed for %r: %s", group, exc)
        return ()
    raw_allow = os.environ.get("YT_UNIQ_PLUGINS_ALLOWLIST", "").strip()
    if not raw_allow:
        return tuple(eps)
    allowed = {tok.strip() for tok in raw_allow.split(",") if tok.strip()}
    return tuple(ep for ep in eps if ep.name in allowed)
