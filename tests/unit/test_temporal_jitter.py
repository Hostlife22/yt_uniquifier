"""video.temporal_jitter — deterministic timestamp-bucket perturbations."""

from __future__ import annotations

import random
import re

import pytest
from pydantic import ValidationError

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_temporal_jitter import (
    TIME_BUCKETS_PER_SECOND,
    WINDOW_BUCKETS,
    WINDOW_FRAMES,
    WINDOW_SECONDS,
    TemporalJitterParams,
)


def test_fallback_rng_uses_rng_seed_deterministically() -> None:
    """Regression: when rng is not supplied, the builder must use
    params.rng_seed as a deterministic fallback, not a time-seeded
    Random() — otherwise a resumed run produces different blackout
    indices than the partial encode already on disk.
    """
    spec = get("video.temporal_jitter")
    params = TemporalJitterParams(rng_seed=12345)
    a = call_build(spec, params, LabelAllocator(), "0:v:0", rng=None)
    b = call_build(spec, params, LabelAllocator(), "0:v:0", rng=None)
    assert a.filter_str == b.filter_str, (
        "Two calls with the same rng_seed must produce identical "
        "filter_str; got divergent strings"
    )


def test_default_emits_blackout_and_drop() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(),
        LabelAllocator(), "0:v:0", rng=random.Random(0),
    )
    assert WINDOW_FRAMES == WINDOW_BUCKETS  # compatibility alias
    assert f"floor(mod(T\\,{WINDOW_SECONDS})*{TIME_BUCKETS_PER_SECOND})" in chain.filter_str
    assert f"floor(mod(t\\,{WINDOW_SECONDS})*{TIME_BUCKETS_PER_SECOND})" in chain.filter_str
    assert "mod(N" not in chain.filter_str
    assert "mod(n" not in chain.filter_str
    assert "geq=" in chain.filter_str
    assert "select=" in chain.filter_str


def test_max_probabilities_keep_filter_expression_bounded() -> None:
    """Allowed maxima must not create an enormous FFmpeg expression."""
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_prob=0.2, drop_prob=0.2),
        LabelAllocator(), "0:v:0", rng=random.Random(0),
    )
    assert len(chain.filter_str) < 1_000
    assert chain.filter_str.count("floor(mod(T") == 3
    assert chain.filter_str.count("floor(mod(t") == 1


def test_blackout_blur_uses_mid_gray() -> None:
    """blackout_blur=true sets Y replacement to 128 (mid-gray)."""
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_blur=True, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(7),
    )
    # Last argument before p(X,Y) in lum expression is the replacement value.
    # Pattern: lum='if(<cond>\,<repl>\,p(X\,Y))'
    m = re.search(r"lum='if\(.*?\)\\,(\d+)\\,p\(X\\,Y\)\)'", chain.filter_str)
    assert m and m.group(1) == "128", f"expected Y=128 for blur, got match={m}"


def test_blackout_blur_false_uses_pure_black() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_blur=False, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(7),
    )
    m = re.search(r"lum='if\(.*?\)\\,(\d+)\\,p\(X\\,Y\)\)'", chain.filter_str)
    assert m and m.group(1) == "0", f"expected Y=0 for hard black, got match={m}"


def test_no_index_overlap_between_blackout_and_drop() -> None:
    """Blackout and drop occupy adjacent, disjoint permutation ranks."""
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_prob=0.1, drop_prob=0.1),
        LabelAllocator(), "0:v:0", rng=random.Random(42),
    )
    expected_count = round(WINDOW_BUCKETS * 0.1)
    assert f"\\,{expected_count})" in chain.filter_str
    assert f"\\,{expected_count}\\,{expected_count * 2 - 1})" in chain.filter_str


def test_all_probs_zero_returns_null_passthrough() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_prob=0, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(0),
    )
    assert chain.filter_str == "null"


def test_blackout_prob_above_max_rejected() -> None:
    """Schema bounds: blackout_prob must be ≤ 0.2."""
    with pytest.raises(ValidationError):
        TemporalJitterParams(blackout_prob=0.5)


def test_same_seed_reproducible() -> None:
    spec = get("video.temporal_jitter")
    p = TemporalJitterParams(blackout_prob=0.05, drop_prob=0.05)
    c1 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str


def test_different_seeds_produce_different_indices() -> None:
    """rng seed change → different sampled frame indices."""
    spec = get("video.temporal_jitter")
    p = TemporalJitterParams(blackout_prob=0.05, drop_prob=0.0)
    c1 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(2))
    assert c1.filter_str != c2.filter_str
