"""audio.noise_overlay — parametric pink/white/brown noise mixed via amix.

Source: Smitelli (2010). At -10 dB (~30 %) mix, chromaprint shifts
significantly while speech stays intelligible.
"""

from __future__ import annotations

import random

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_noise_overlay import NoiseOverlayParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


def test_default_filter_shape() -> None:
    spec = get("audio.noise_overlay")
    chain = call_build(spec, NoiseOverlayParams(), LabelAllocator(), "0:a:0")
    # anull (passthrough) → anoisesrc (source) → amix (combine)
    assert chain.filter_str.startswith("anull[")
    assert "anoisesrc=c=pink:r=48000:amplitude=1" in chain.filter_str
    assert "amix=inputs=2:weights=" in chain.filter_str


def test_db_to_weight_conversion() -> None:
    """-12 dB → amplitude ratio 10**(-12/20) ≈ 0.2512."""
    spec = get("audio.noise_overlay")
    chain = call_build(
        spec, NoiseOverlayParams(noise_db=-12.0, randomize_within_db=0),
        LabelAllocator(), "0:a:0",
    )
    # Noise weight 0.2512, main weight 0.7488.
    assert "weights=0.7488 0.2512" in chain.filter_str


def test_color_param_propagates() -> None:
    spec = get("audio.noise_overlay")
    chain = call_build(
        spec, NoiseOverlayParams(color="brown"),
        LabelAllocator(), "0:a:0",
    )
    assert "anoisesrc=c=brown" in chain.filter_str


def test_randomize_within_db_seeded_reproducible() -> None:
    spec = get("audio.noise_overlay")
    p = NoiseOverlayParams(noise_db=-12.0, randomize_within_db=4.0)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str


def test_randomize_within_db_different_seeds_differ() -> None:
    spec = get("audio.noise_overlay")
    p = NoiseOverlayParams(noise_db=-12.0, randomize_within_db=4.0)
    pairs = [(1, 2), (10, 20), (100, 200)]
    assert any(
        call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(a)).filter_str
        != call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(b)).filter_str
        for a, b in pairs
    )


def test_no_randomize_is_deterministic_without_rng() -> None:
    spec = get("audio.noise_overlay")
    p = NoiseOverlayParams(noise_db=-10.0, randomize_within_db=0)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(99))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=None)
    assert c1.filter_str == c2.filter_str
