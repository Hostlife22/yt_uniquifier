"""audio.loudnorm target_jitter_lufs — per-run integrated target shift."""

from __future__ import annotations

import random

import pytest

from yt_uniquifier.core.transforms.audio_loudnorm import (
    LoudnormMeasurement,
    LoudnormParams,
    build_apply,
)
from yt_uniquifier.core.transforms.base import LabelAllocator


def _measurement() -> LoudnormMeasurement:
    return LoudnormMeasurement(
        input_i=-21.8, input_tp=-3.2, input_lra=5.1,
        input_thresh=-32.6, target_offset=0.04,
    )


def _extract_integrated(filt: str) -> float:
    # filter_str: "loudnorm=I=<integrated>:TP=...:LRA=...:..."
    head = filt.split("I=", 1)[1]
    val = head.split(":", 1)[0]
    return float(val)


def test_zero_jitter_deterministic() -> None:
    p = LoudnormParams(integrated=-14.0, target_jitter_lufs=0.0)
    c1 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str == c2.filter_str
    assert _extract_integrated(c1.filter_str) == -14.0


def test_no_rng_no_jitter() -> None:
    """rng=None disables jitter even if param is non-zero."""
    p = LoudnormParams(integrated=-14.0, target_jitter_lufs=2.0)
    c = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=None)
    assert _extract_integrated(c.filter_str) == -14.0


def test_jitter_seeded_reproducible() -> None:
    p = LoudnormParams(integrated=-14.0, target_jitter_lufs=2.0)
    c1 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(7))
    c2 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(7))
    assert c1.filter_str == c2.filter_str   # same seed → identical
    target = _extract_integrated(c1.filter_str)
    assert target != -14.0
    assert -16.0 <= target <= -12.0          # within ±2 LUFS


def test_jitter_different_seeds_differ() -> None:
    p = LoudnormParams(integrated=-14.0, target_jitter_lufs=2.0)
    c1 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = build_apply(p, _measurement(), LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str != c2.filter_str


def test_jitter_respects_bounds_via_pydantic() -> None:
    """schema rejects out-of-range jitter."""
    with pytest.raises(Exception):  # noqa: B017
        LoudnormParams(target_jitter_lufs=10.0)
    with pytest.raises(Exception):  # noqa: B017
        LoudnormParams(target_jitter_lufs=-1.0)
