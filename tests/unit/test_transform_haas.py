"""audio.haas_stereo — adelay-based mono-compatible stereo widener.

Source: Smitelli (2010) audio CID analysis.
"""

from __future__ import annotations

import random

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_haas import HaasStereoParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


def test_default_filter_shape() -> None:
    spec = get("audio.haas_stereo")
    chain = call_build(spec, HaasStereoParams(), LabelAllocator(), "0:a:0")
    assert chain.filter_str == "adelay=0|15"


def test_delay_param_propagates() -> None:
    spec = get("audio.haas_stereo")
    chain = call_build(
        spec, HaasStereoParams(delay_ms=25.0),
        LabelAllocator(), "0:a:0",
    )
    assert chain.filter_str == "adelay=0|25"


def test_randomize_within_seeded_reproducible() -> None:
    spec = get("audio.haas_stereo")
    p = HaasStereoParams(delay_ms=15.0, randomize_within_ms=8.0)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str


def test_randomize_within_different_seeds_differ() -> None:
    spec = get("audio.haas_stereo")
    p = HaasStereoParams(delay_ms=15.0, randomize_within_ms=8.0)
    # With wide jitter (±8 ms) and two seeds, integer-rounded delays
    # should almost certainly differ. Try a handful of seed pairs.
    pairs = [(1, 2), (10, 20), (100, 200), (1000, 2000)]
    assert any(
        call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(a)).filter_str
        != call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(b)).filter_str
        for a, b in pairs
    )


def test_randomize_within_clamped_to_range() -> None:
    """Even with extreme rng values, delay stays in [1, 40] ms."""
    spec = get("audio.haas_stereo")
    p = HaasStereoParams(delay_ms=5.0, randomize_within_ms=10.0)
    # Brute-force many seeds; clamping should always hold.
    for seed in range(50):
        chain = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(seed))
        delay = int(chain.filter_str.split("|")[1])
        assert 1 <= delay <= 40, f"delay {delay} out of range for seed {seed}"


def test_no_randomize_is_deterministic_without_rng() -> None:
    spec = get("audio.haas_stereo")
    p = HaasStereoParams(delay_ms=20.0, randomize_within_ms=0.0)
    c = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(99))
    assert c.filter_str == "adelay=0|20"
