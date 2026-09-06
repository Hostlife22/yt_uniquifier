"""Real FFmpeg equivalence of bounded QA representations."""
import hashlib
import subprocess
from pathlib import Path

import imagehash
import pytest

from tools.media_diagnostics import decoded_timeline
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import build_plan
from yt_uniquifier.core.qa import phash, registration
from yt_uniquifier.core.qa.ssim import compute as compute_ssim
from yt_uniquifier.core.qa.vmaf import compute as compute_vmaf


@pytest.mark.integration
def test_streamed_hashes_match_legacy_grid(tiny_clip: Path):
    legacy = [int(str(imagehash.phash(frame)), 16) for frame in phash.sample_frames(tiny_clip, 601)]
    assert phash._sample_hashes(tiny_clip, 601) == legacy


@pytest.mark.integration
def test_virtual_reference_matches_materialized(tiny_clip, tmp_path, isolated_cache, monkeypatch):
    plan = build_plan(tiny_clip, Profile(name="bounded"), encoder_override="libx264")
    physical = registration.build_transformed_reference(
        plan, tmp_path / "physical" / "ref.mkv", target_segment_sec=1,
    )
    video = plan.source.video[0]
    estimate = int(video.width * video.height * video.fps * plan.source.duration_sec * 0.5)
    monkeypatch.setenv("YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES", str(estimate + 1))
    virtual = registration.build_transformed_reference(
        plan, tmp_path / "virtual ' path" / "ref.mkv", target_segment_sec=1,
    )
    assert virtual.path.suffix == ".ffconcat"
    assert not virtual.path.with_suffix(".mkv").exists()

    def pixels(path):
        command = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        return hashlib.sha256(subprocess.check_output(command, timeout=60)).hexdigest()

    assert pixels(virtual.path) == pixels(physical.path)
    assert decoded_timeline(virtual.path)["streams"] == decoded_timeline(physical.path)["streams"]
    assert compute_ssim(virtual.path, physical.path, reset_pts=True).score == pytest.approx(1.0)
    metric = compute_vmaf(virtual.path, physical.path, reset_pts=True)
    if metric.score is not None:  # libvmaf is optional on supported FFmpeg installations.
        assert metric.score > 99.0
