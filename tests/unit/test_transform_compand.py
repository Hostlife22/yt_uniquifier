"""audio.compand transform — default filter shape + randomization."""

from __future__ import annotations

import random

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_compand import CompandParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


def test_default_filter_shape() -> None:
    spec = get("audio.compand")
    chain = call_build(
        spec, CompandParams(randomize_within=False),
        LabelAllocator(), "0:a:0", rng=None,
    )
    # attack/decay first, then transfer points.
    assert chain.filter_str.startswith("compand=attacks=0.05:decays=0.5:")
    assert "points=-80/-80|-20.00/-8.00|0/-3" in chain.filter_str


def test_no_randomize_is_deterministic() -> None:
    spec = get("audio.compand")
    p = CompandParams(threshold_db=-20.0, ratio=2.5, randomize_within=False)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(2))
    # Without randomize_within, rng doesn't change the output.
    assert c1.filter_str == c2.filter_str


def test_randomize_same_seed_reproducible() -> None:
    spec = get("audio.compand")
    p = CompandParams(threshold_db=-20.0, ratio=2.5, randomize_within=True)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str


def test_randomize_different_seeds_differ() -> None:
    spec = get("audio.compand")
    p = CompandParams(threshold_db=-20.0, ratio=2.5, randomize_within=True)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str != c2.filter_str
