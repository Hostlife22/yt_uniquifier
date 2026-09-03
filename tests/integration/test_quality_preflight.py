"""Real-media regressions for quality feedback preflight guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.errors import PreflightFailure
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@needs_ffmpeg
@pytest.mark.integration
def test_unregistered_target_vmaf_stops_before_first_encode(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    profile = load_profile(PROFILES_DIR / "soft.yaml").model_copy(update={
        "target_vmaf": 95.0,
        "target_vmaf_max_retries": 2,
        "skip_watermark_check": True,
    })
    plan = build_plan(tiny_clip, profile, encoder_override="libx264")
    work_dir = tmp_path / "work"

    with pytest.raises(
        PreflightFailure,
        match="quality.target_vmaf.unregistered_reference",
    ):
        run_full(
            plan,
            RunOptions(
                work_dir=work_dir,
                output=tmp_path / "output.mp4",
                target_segment_sec=600.0,
            ),
        )

    assert not list(work_dir.glob("seg_*.mkv"))
