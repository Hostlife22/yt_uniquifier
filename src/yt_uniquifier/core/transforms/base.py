"""Transform registry and supporting primitives.

Each transform is a small unit that, given its params and the previous label,
emits a `FilterChain` — one fragment of the ffmpeg `-filter_complex` graph.

The pipeline builder concatenates fragments linearly. A transform never knows
which transform comes before or after it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

Kind = Literal["video", "audio"]


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
    build: Callable[[BaseModel, LabelAllocator, str], FilterChain]
    defaults: dict[str, object] = field(default_factory=dict)
    incompatible_with: tuple[str, ...] = ()


_REGISTRY: dict[str, TransformSpec] = {}


def register(spec: TransformSpec) -> None:
    if spec.id in _REGISTRY:
        raise ValueError(f"transform {spec.id!r} already registered")
    _REGISTRY[spec.id] = spec


def get(transform_id: str) -> TransformSpec:
    if transform_id not in _REGISTRY:
        raise KeyError(f"unknown transform: {transform_id!r}")
    return _REGISTRY[transform_id]


def all_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def reset_for_tests() -> None:
    """Clear the registry. Only call from tests that need a clean slate."""
    _REGISTRY.clear()
