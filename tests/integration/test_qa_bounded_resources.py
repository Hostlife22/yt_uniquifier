"""Real FFmpeg equivalence of bounded QA representations."""
import hashlib
import subprocess
from pathlib import Path

import imagehash
import pytest

from tools.media_diagnostics import decoded_timeline
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile, Segment
from yt_uniquifier.core.orchestrator import build_plan
from yt_uniquifier.core.qa import phash, registration
from yt_uniquifier.core.qa.ssim import compute as compute_ssim
from yt_uniquifier.core.qa.vmaf import compute as compute_vmaf


@pytest.mark.integration
def test_streamed_hashes_match_legacy_grid(tiny_clip: Path):
    legacy = [int(str(imagehash.phash(frame)), 16) for frame in phash.sample_frames(tiny_clip, 601)]
    assert phash._sample_hashes(tiny_clip, 601) == legacy


@pytest.mark.integration
@pytest.mark.parametrize("cadence", ["cfr", "fractional", "vfr"])
def test_virtual_reference_matches_materialized(
    tiny_clip, tmp_path, isolated_cache, monkeypatch, cadence,
):
    source = tiny_clip
    if cadence != "cfr":
        source = tmp_path / "source.mkv"
        command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                   "testsrc2=size=320x180:rate=30000/1001:duration=3.7"]
        if cadence == "vfr":
            command.extend(["-vf", "select='not(mod(n,2))+not(mod(n,5))'", "-fps_mode", "vfr"])
        command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-g", "15", str(source)])
        subprocess.run(command, capture_output=True, check=True, timeout=60)
    plan = build_plan(source, Profile(name="bounded"), encoder_override="libx264")
    if cadence == "vfr":
        # Isolate reference storage from FFmpeg 9's post-scan stream.start_time
        # mutation (tracked separately in RISK_REGISTER.md). These are actual
        # keyframes of this generated 30000/1001 select/GOP fixture.
        monkeypatch.setattr(registration, "plan_segments", lambda *_args: [
            Segment(idx=0, start_sec=0.0, end_sec=1.668),
            Segment(idx=1, start_sec=1.668, end_sec=plan.source.duration_sec),
        ])
    physical = registration.build_transformed_reference(
        plan, tmp_path / "physical" / "ref.mkv", target_segment_sec=1,
    )
    video = plan.source.video[0]
    estimate = int(video.width * video.height * video.fps * plan.source.duration_sec * 0.5)
    monkeypatch.setenv("YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES", str(estimate + 1))
    virtual = registration.build_transformed_reference(
        plan, tmp_path / "virtual ' path" / "ref.mkv", target_segment_sec=1,
    )
    if cadence != "cfr":
        assert virtual.segments >= 2
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


@pytest.mark.integration
def test_measured_reference_budget_prevents_publication(
    tiny_clip, tmp_path, isolated_cache, monkeypatch,
):
    plan = build_plan(tiny_clip, Profile(name="bounded"), encoder_override="libx264")
    # Admit deliberately so real encoder writes exercise the live check, not
    # the separate planning estimate. No real filesystem is filled by this test.
    monkeypatch.setattr(registration, "_check_reference_budget", lambda *_args: False)
    monkeypatch.setenv("YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES", "16")
    destination = tmp_path / "reference.mkv"
    with pytest.raises(PipelineError, match="measured disk budget"):
        registration.build_transformed_reference(plan, destination)
    assert not destination.exists()
