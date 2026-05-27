"""Divergent per-segment seed strategy.

Source: Fojcik & Syga 2025 — temporal attacks exploit run-level uniformity
of transform parameters across segments. Divergent seeds break that.
"""

from __future__ import annotations

from yt_uniquifier.core.seed_resolver import derive_segment_seed
from yt_uniquifier.core.segmenter import _plan_for_segment


def test_divergent_deterministic_for_same_triple() -> None:
    """Same (plan_hash, idx, run_seed) always produces the same seed."""
    a = derive_segment_seed("abcd1234", 0, 999)
    b = derive_segment_seed("abcd1234", 0, 999)
    assert a == b


def test_divergent_different_segments_different_seeds() -> None:
    """Different segment idx produces different seeds for same run."""
    seeds = {derive_segment_seed("abcd1234", i, 999) for i in range(20)}
    assert len(seeds) == 20  # all distinct


def test_divergent_different_run_seeds_different() -> None:
    """Different run_seed (e.g. two invocations) → different per-seg seeds."""
    a = derive_segment_seed("abcd1234", 5, 100)
    b = derive_segment_seed("abcd1234", 5, 200)
    assert a != b


def test_divergent_seed_is_uint32() -> None:
    """Result must fit in uint32 for clean JSON round-trip."""
    for i in range(10):
        s = derive_segment_seed("hash", i, 12345)
        assert 0 <= s < 2**32


def test_plan_for_segment_passthrough_when_not_divergent(monkeypatch) -> None:
    """seed_strategy != divergent → plan returned unchanged."""
    import tempfile
    from pathlib import Path

    from tests.unit.test_pipeline_graph import _plan, _src
    from yt_uniquifier.core.models import TransformConfig

    tmp = Path(tempfile.mkdtemp())
    src = _src(tmp)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    # default seed_strategy is per_run; check that _plan_for_segment is a no-op
    assert plan.profile.seed_strategy == "per_run"
    out = _plan_for_segment(plan, 7)
    assert out is plan  # exact object identity — no copy


def test_plan_for_segment_diverges_seed_when_strategy_set() -> None:
    """seed_strategy=divergent → returned plan has a per-segment derived seed."""
    import tempfile
    from pathlib import Path

    from tests.unit.test_pipeline_graph import _plan, _src
    from yt_uniquifier.core.models import TransformConfig

    tmp = Path(tempfile.mkdtemp())
    src = _src(tmp)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    # Mutate seed_strategy on a fresh profile copy.
    div_profile = plan.profile.model_copy(update={"seed_strategy": "divergent"})
    div_plan = plan.model_copy(update={"profile": div_profile, "run_seed": 555})

    out0 = _plan_for_segment(div_plan, 0)
    out1 = _plan_for_segment(div_plan, 1)
    out2 = _plan_for_segment(div_plan, 2)

    # Different segments → different seeds.
    assert out0.run_seed != out1.run_seed
    assert out1.run_seed != out2.run_seed
    assert out0.run_seed != out2.run_seed

    # Plan_hash and profile remain unchanged in the copy.
    assert out0.plan_hash == div_plan.plan_hash
    assert out0.profile.seed_strategy == "divergent"

    # Same idx, same input plan → same derived seed (resume-stable).
    out0_again = _plan_for_segment(div_plan, 0)
    assert out0.run_seed == out0_again.run_seed
