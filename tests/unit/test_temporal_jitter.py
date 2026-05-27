"""video.temporal_jitter — periodic blackout/drop with rng-driven phase.

Source: Fojcik & Syga, arXiv:2501.11171 (2025) — random frame perturbation
breaks neural video copy detectors. We use deterministic period + random
phase to avoid ffmpeg's expression-level random() (version-dependent).
"""

from __future__ import annotations

import random

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_temporal_jitter import TemporalJitterParams


def test_default_emits_blackout_and_drop() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(),
        LabelAllocator(), "0:v:0", rng=random.Random(0),
    )
    # Period for blackout_prob=0.033 → round(1/0.033)=30.
    # Period for drop_prob=0.025 → round(1/0.025)=40.
    assert "geq=" in chain.filter_str
    assert "mod(N\\,30)" in chain.filter_str
    assert "select=" in chain.filter_str
    assert "mod(n\\,40)" in chain.filter_str


def test_blackout_blur_uses_mid_gray() -> None:
    """blackout_blur=true sets Y channel to 128 (mid-gray) — soft flash."""
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_blur=True, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(7),
    )
    # The Y replacement constant when condition fires is 128 (mid-gray).
    # Find the y='if(...,N,val)' branch and check the replacement value.
    y_part = chain.filter_str.split("lum='if(")[1].split("'")[0]
    # Expression form: eq(mod(N\,30)\,<offset>)\,<repl>\,val
    repl_y = y_part.split("\\,")[3]
    assert repl_y == "128", f"expected Y replacement 128 (mid-gray), got {repl_y!r}"


def test_blackout_blur_false_uses_pure_black() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_blur=False, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(7),
    )
    y_part = chain.filter_str.split("lum='if(")[1].split("'")[0]
    repl_y = y_part.split("\\,")[3]
    assert repl_y == "0", f"expected Y replacement 0 (pure black), got {repl_y!r}"


def test_all_probs_zero_returns_null_passthrough() -> None:
    spec = get("video.temporal_jitter")
    chain = call_build(
        spec, TemporalJitterParams(blackout_prob=0, drop_prob=0),
        LabelAllocator(), "0:v:0", rng=random.Random(0),
    )
    assert chain.filter_str == "null"


def test_blackout_prob_above_max_rejected() -> None:
    """Schema bounds: blackout_prob must be ≤ 0.2 (visibly destructive above)."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TemporalJitterParams(blackout_prob=0.5)


def test_rng_phase_varies_between_seeds() -> None:
    """Same params, different rng seeds → different phase offsets."""
    spec = get("video.temporal_jitter")
    p = TemporalJitterParams(blackout_prob=0.05, drop_prob=0.05)
    pairs = [(1, 2), (10, 20), (100, 200), (1000, 2000)]
    assert any(
        call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(a)).filter_str
        != call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(b)).filter_str
        for a, b in pairs
    )


def test_same_seed_reproducible() -> None:
    spec = get("video.temporal_jitter")
    p = TemporalJitterParams(blackout_prob=0.05, drop_prob=0.05)
    c1 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:v:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str
