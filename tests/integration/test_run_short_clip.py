"""End-to-end run on a 2-second clip through soft and medium profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.pipeline import FilterGraph, compute_plan_hash
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import run

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("profile_name", ["soft", "medium"])
def test_run_short_clip_through_profile(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path, profile_name: str
) -> None:
    out = tmp_path / f"out_{profile_name}.mp4"

    source = probe(tiny_clip)
    profile = load_profile(PROFILES_DIR / f"{profile_name}.yaml")
    enc = pick_encoder(detect_encoders(), prefer=["libx264"], codec=profile.target_codec)
    plan = Plan(
        source=source,
        profile=profile,
        encoder=enc,
        plan_hash=compute_plan_hash(source, profile, enc),
    )

    built = FilterGraph(plan, out).build()
    res = run(built, output=out)
    assert res.returncode == 0
    assert out.exists()
    assert out.stat().st_size > 0

    # probe the output to confirm it's a valid mp4 with our expected dims.
    out_meta = probe(out)
    assert out_meta.container in {"mp4", "mov"}
    assert len(out_meta.video) == 1
    v = out_meta.video[0]
    # Crop+rescale + even-rounding may shave up to 2 px.
    assert 316 <= v.width <= 320
    assert 178 <= v.height <= 180
    assert v.width % 2 == 0
    assert v.height % 2 == 0


@needs_ffmpeg
@pytest.mark.integration
def test_run_aggressive_profile(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    out = tmp_path / "out_aggressive.mp4"
    source = probe(tiny_clip)
    profile = load_profile(PROFILES_DIR / "aggressive.yaml")
    enc = pick_encoder(detect_encoders(), prefer=["libx264"], codec="h264")
    plan = Plan(
        source=source,
        profile=profile,
        encoder=enc,
        plan_hash=compute_plan_hash(source, profile, enc),
    )
    built = FilterGraph(plan, out).build()
    run(built, output=out)
    assert out.exists() and out.stat().st_size > 0
