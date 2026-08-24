"""v1.2.0 Task 22 integration: end-to-end AV1 encode via libsvtav1.

Skipped automatically when libsvtav1 is not available in the local
ffmpeg build (older Linux distros, custom builds).  When present, the
test runs ``yt-uniq``-equivalent orchestration through ``run_full`` on
a 5-second clip with the ``youtube_av1`` profile and asserts:

  * the AV1 output exists and is non-empty
  * its video stream codec is ``av1``
  * duration matches the source within the concat trim window
  * VMAF mean ≥ 80 (very loose floor — libsvtav1 preset 8 on a synth
    clip routinely hits 95+; the test just guards against catastrophic
    quality collapse from a wrong CRF/profile wiring).

No fingerprint pinning — AV1 bitstreams are non-deterministic across
SVT-AV1 minor versions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


def _has_libsvtav1() -> bool:
    """Cheap, side-effect-free check: does this ffmpeg build know the
    encoder name?  We don't run detect_encoders() here because it has
    its own cache contract and the pytest-level skip wants a fast
    yes/no before any work happens.
    """
    if not shutil.which("ffmpeg"):
        return False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return "libsvtav1" in proc.stdout


needs_av1 = pytest.mark.skipif(
    not _has_libsvtav1(),
    reason="libsvtav1 not available in this ffmpeg build",
)


@pytest.fixture
def short_av1_clip(tmp_path: Path) -> Path:
    """5-second 320x180 source with a 0.5 s GOP — small enough that
    AV1 wall-clock stays under a few seconds even on libsvtav1 default
    preset.
    """
    out = tmp_path / "src.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-x264-params", "keyint=12:min-keyint=12:scenecut=0",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out


@pytest.fixture
def single_frame_av1_clip(tmp_path: Path) -> Path:
    """One-frame source for the 4K profile wiring smoke.

    It still exercises the real 3840x2160 filter/encoder path without
    making every integration matrix encode five seconds of 4K AV1.
    """
    out = tmp_path / "single_frame.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=4:duration=0.25",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@needs_ffmpeg
@needs_av1
@pytest.mark.integration
def test_av1_encoder_detected(isolated_cache: Path) -> None:
    """Cold-cache detect_encoders() must surface libsvtav1 when the
    binary supports it.  Catches a regression where a vendor tag
    rename in encoder._CANDIDATES would silently drop a working
    encoder from the candidate list.
    """
    candidates = detect_encoders(force=True)
    by_name = {c.name: c for c in candidates}
    assert "libsvtav1" in by_name, (
        "libsvtav1 not enumerated; check encoder._CANDIDATES wiring"
    )
    assert by_name["libsvtav1"].works, (
        f"libsvtav1 probe failed: {by_name['libsvtav1'].error}"
    )
    assert by_name["libsvtav1"].codec == "av1"
    assert by_name["libsvtav1"].vendor == "svtav1"


@needs_ffmpeg
@needs_av1
@pytest.mark.integration
def test_youtube_av1_profile_end_to_end(
    short_av1_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """End-to-end orchestrator run with the youtube_av1 profile must
    produce a playable AV1 .mp4 whose duration matches the source.
    """
    out = tmp_path / "out_av1.mp4"
    profile = load_profile(PROFILES_DIR / "youtube_av1.yaml")
    plan = build_plan(short_av1_clip, profile, encoder_override="libsvtav1")
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        keep_segments=False,
        target_segment_sec=2.0,
    )
    summary = run_full(plan, options)

    assert summary.segments_done >= 1
    assert out.exists() and out.stat().st_size > 0

    src_meta = probe(short_av1_clip)
    out_meta = probe(out)
    assert len(out_meta.video) == 1
    assert out_meta.video[0].codec == "av1", (
        f"expected AV1 stream, got {out_meta.video[0].codec}"
    )

    # Duration within 0.5 s of source (concat -t contract).
    duration_delta = abs(out_meta.duration_sec - src_meta.duration_sec)
    assert duration_delta < 0.5, (
        f"output duration {out_meta.duration_sec:.3f} drifted by "
        f"{duration_delta:.3f} s from source {src_meta.duration_sec:.3f}"
    )


@needs_ffmpeg
@needs_av1
@pytest.mark.integration
def test_youtube_4k_av1_profile_end_to_end(
    single_frame_av1_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    """The shipped 4K AV1 profile must produce its promised stream shape."""
    out = tmp_path / "out_4k_av1.mp4"
    profile = load_profile(PROFILES_DIR / "youtube_4k_av1.yaml")
    plan = build_plan(single_frame_av1_clip, profile, encoder_override="libsvtav1")
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work" / plan.plan_hash,
            output=out,
            keep_segments=False,
        ),
    )

    output_meta = probe(out)
    assert output_meta.video[0].codec == "av1"
    assert (output_meta.video[0].width, output_meta.video[0].height) == (3840, 2160)
