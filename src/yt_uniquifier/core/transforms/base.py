"""Transform registry and supporting primitives.

Each transform is a small unit that, given its params and the previous label,
emits a `FilterChain` — one fragment of the ffmpeg `-filter_complex` graph.

The pipeline builder concatenates fragments linearly. A transform never knows
which transform comes before or after it.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from random import Random
from typing import Literal, TypeVar

from pydantic import BaseModel

from yt_uniquifier.core.errors import PipelineError

Kind = Literal["video", "audio"]

P = TypeVar("P", bound=BaseModel)


def ensure_params(params: BaseModel, expected: type[P]) -> P:
    """Runtime-checked downcast of a builder's ``params`` argument.

    Replaces the ``assert isinstance(params, XParams)`` pattern that was
    sprinkled across every transform builder. ``assert`` is stripped
    under ``python -O`` / ``PYTHONOPTIMIZE=1`` (PyInstaller release
    builds, some Docker base images), at which point a wrong-schema
    ``BaseModel`` would silently flow through and the builder would
    either produce a wrong filter string or ``AttributeError`` on the
    next field access. Raise explicitly so the failure is visible and
    typed even under ``-O``.
    """
    if not isinstance(params, expected):
        raise PipelineError(
            f"transform builder expected {expected.__name__}, "
            f"got {type(params).__name__}",
        )
    return params


def ensure_rng(rng: object) -> Random:
    """Runtime-checked downcast of an rng kwarg to ``random.Random``.

    Same motivation as :func:`ensure_params`: builders previously did
    ``assert isinstance(rng, Random)`` which becomes a no-op under
    ``-O``. A non-Random object that happens not to have ``.uniform``
    would then ``AttributeError`` instead of failing with a typed
    domain error.
    """
    if not isinstance(rng, Random):
        raise PipelineError(
            f"transform builder expected random.Random, "
            f"got {type(rng).__name__}",
        )
    return rng

# Builders may accept an optional `rng` keyword for per-run randomization.
# Backward-compat: builders that don't need it ignore the keyword.
BuildFn = Callable[..., "FilterChain"]


class LabelAllocator:
    """Hands out unique ffmpeg labels: v1, v2, ... and a1, a2, ..."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {"v": 0, "a": 0}

    def next(self, kind: Literal["v", "a"]) -> str:
        self._counters[kind] += 1
        return f"{kind}{self._counters[kind]}"


@dataclass(frozen=True)
class FilterChain:
    """One transform's contribution to filter_complex.

    Composed as `[<in_label>] <filter_str> [<out_label>]` by the pipeline.

    `extra_inputs` is a list of file paths that this transform requires as
    additional `-i` inputs (e.g. blend_b needs a B-video). The pipeline
    assigns input indices and rewrites references in the filter graph.
    """

    in_label: str
    out_label: str
    filter_str: str
    extra_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransformSpec:
    id: str
    kind: Kind
    schema: type[BaseModel]
    # Callable signature: (params, allocator, in_label, *, rng=None) -> FilterChain
    # Old builders that don't need rng simply ignore the kwarg.
    build: BuildFn
    defaults: dict[str, object] = field(default_factory=dict)
    incompatible_with: tuple[str, ...] = ()


_REGISTRY: dict[str, TransformSpec] = {}
# v1.2.0 Task 23 — spec ids registered while a plugin manifest was
# active.  ``call_build`` consults this set to decide whether to wrap
# the builder invocation in the audit-hook sandbox; built-in transforms
# bypass the sandbox so legitimate IO (e.g. video.subtitles reading a
# .srt file) keeps working unchanged.
_PLUGIN_SPEC_IDS: set[str] = set()


def register(spec: TransformSpec) -> None:
    # v1.2.0 Task 23 — capability gate: if a plugin manifest is active
    # (set by ``_discover_third_party`` before importing the plugin's
    # entry point), the plugin's capabilities must include the kind
    # being registered.  Built-ins and tests run with no active
    # manifest and pass through unchanged.  Imported here rather than
    # at module top to avoid a hard import cycle: plugins ↔ transforms.
    from yt_uniquifier.core.plugins import (
        assert_kind_allowed,
        get_active_manifest,
        record_plugin_spec,
    )
    assert_kind_allowed(spec.kind)
    if spec.id in _REGISTRY:
        raise ValueError(f"transform {spec.id!r} already registered")
    _REGISTRY[spec.id] = spec
    active = get_active_manifest()
    if active is not None:
        _PLUGIN_SPEC_IDS.add(spec.id)
        record_plugin_spec(active.name, spec.id)


def get(transform_id: str) -> TransformSpec:
    if transform_id not in _REGISTRY:
        raise KeyError(f"unknown transform: {transform_id!r}")
    return _REGISTRY[transform_id]


def all_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def reset_for_tests() -> None:
    """Clear the registry. Only call from tests that need a clean slate."""
    _REGISTRY.clear()
    _PLUGIN_SPEC_IDS.clear()


@functools.cache
def _builder_accepts_rng(build_fn: BuildFn) -> bool:
    """Cache `rng`-acceptance per build callable.

    Inspecting the signature once at first use avoids a `try/except
    TypeError` fallback that would otherwise silently swallow genuine
    TypeErrors raised inside the builder body (e.g. wrong operand types in
    a filter string expression). LRU-cache is unbounded but the number of
    distinct builder callables is fixed at process startup.
    """
    try:
        sig = inspect.signature(build_fn)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "rng" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def call_build(
    spec: TransformSpec,
    params: BaseModel,
    alloc: LabelAllocator,
    in_label: str,
    *,
    rng: Random | None = None,
) -> FilterChain:
    """Invoke a builder, transparently passing rng if it accepts it.

    Lets old builders stay positional-only without burdening every transform
    with `**_` boilerplate. Signature is inspected once and cached, so a
    real TypeError raised inside the builder propagates instead of being
    masked by a try/except fallback.

    v1.2.0 Task 23 — if ``spec`` was registered by a third-party plugin
    (tracked in :data:`_PLUGIN_SPEC_IDS`), the builder runs inside the
    audit-hook sandbox so denylisted syscalls (filesystem writes,
    network egress, subprocess spawns) raise ``PluginViolation``
    instead of silently succeeding.  Built-ins skip the sandbox so
    legitimate IO (``video.subtitles`` reading a .srt file) keeps
    working.
    """
    is_plugin = spec.id in _PLUGIN_SPEC_IDS
    if is_plugin:
        # Local import — keeps ``base.py`` free of a hard plugin_sandbox
        # dependency for the built-in code paths that never need the gate.
        from yt_uniquifier.core.plugin_sandbox import in_plugin_code
        with in_plugin_code():
            if _builder_accepts_rng(spec.build):
                return spec.build(params, alloc, in_label, rng=rng)
            return spec.build(params, alloc, in_label)
    if _builder_accepts_rng(spec.build):
        return spec.build(params, alloc, in_label, rng=rng)
    return spec.build(params, alloc, in_label)
