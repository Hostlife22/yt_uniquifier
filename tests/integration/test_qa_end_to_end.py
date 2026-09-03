"""End-to-end QA on a real tiny clip — both identity and post-uniquification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report, verdict

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
    assessment = verdict(report)
    assert assessment.correctness == "valid"
    assert assessment.band != "invalid"


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


@needs_ffmpeg
@pytest.mark.integration
def test_missing_source_audio_makes_report_invalid(
    tiny_clip: Path, tmp_path: Path,
) -> None:
    video_only = tmp_path / "video-only.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(tiny_clip), "-map", "0:v:0", "-c:v", "copy",
            str(video_only),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    report = build_report(
        tiny_clip,
        video_only,
        samples=4,
        run_vmaf=False,
        run_ssim=False,
        run_audio_fp=False,
        predict_cid=False,
    )
    assessment = verdict(report)
    assert assessment.band == "invalid"
    assert assessment.correctness == "invalid"
    assert any("main audio" in reason for reason in assessment.correctness_reasons)


@needs_ffmpeg
@pytest.mark.integration
def test_corrupt_tail_that_still_probes_makes_report_invalid(tmp_path: Path) -> None:
    source = tmp_path / "source-faststart.mp4"
    corrupt = tmp_path / "corrupt-tail.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-movflags", "+faststart", "-shortest", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    payload = source.read_bytes()
    corrupt.write_bytes(payload[:-20_000])

    # The container header remains readable; only a full decode reaches the damage.
    subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", str(corrupt)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    report = build_report(
        source,
        corrupt,
        samples=4,
        run_vmaf=False,
        run_ssim=False,
        run_audio_fp=False,
        predict_cid=False,
    )

    assessment = verdict(report)
    assert assessment.band == "invalid"
    assert any("full output decode failed" in note for note in report.notes)
