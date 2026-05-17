"""End-to-end QA on a real tiny clip — both identity and post-uniquification."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
def test_identity_pair_has_max_similarity(tiny_clip: Path) -> None:
    """Same file vs itself: pHash similarity = 1.0, SSIM = 1.0."""
    report = build_report(
        tiny_clip, tiny_clip,
        samples=20, run_vmaf=False, run_audio_fp=False, run_ssim=True,
    )
    assert report.phash_similarity == pytest.approx(1.0)
    if report.ssim_mean is not None:
        assert report.ssim_mean > 0.99
    assert report.input_md5 == report.output_md5
    assert report.duration_match


@needs_ffmpeg
@pytest.mark.integration
def test_after_uniquification_metrics_in_range(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    out = tmp_path / "out.mp4"
    profile = load_profile(PROFILES_DIR / "medium.yaml")
    plan = build_plan(tiny_clip, profile, encoder_override="libx264")
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        target_segment_sec=600.0,
        enforce_preflight=False,  # tiny clip may not perfectly meet YouTube SR target
    )
    run_full(plan, options)
    assert out.exists()

    report = build_report(
        tiny_clip, out,
        samples=20, run_vmaf=False, run_audio_fp=False, run_ssim=True,
    )
    # MD5 must differ — uniqueness goal.
    assert report.input_md5 != report.output_md5
    # Visually very close on a 2-second testsrc.
    assert report.phash_similarity > 0.7
    # Output duration ≈ input duration.
    assert report.duration_match
