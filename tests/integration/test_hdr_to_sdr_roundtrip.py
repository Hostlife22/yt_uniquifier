"""Real-ffmpeg integration: synthetic HDR clip → cid_aware_hdr_to_sdr → SDR."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


def _have_libx264() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return "libx264" in res.stdout


def _have_zscale() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return "zscale" in res.stdout


def _have_tonemap() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return " tonemap " in res.stdout


needs_tonemap_stack = pytest.mark.skipif(
    not (_have_libx264() and _have_zscale() and _have_tonemap()),
    reason="HDR→SDR tonemap needs ffmpeg with libx264 + zscale + tonemap",
)


@pytest.fixture(scope="session")
def hdr_clip_for_tonemap(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic 2-second 1280x720 clip tagged as PQ/BT.2020."""
    out = tmp_path_factory.mktemp("hdr_tm") / "hdr.mp4"
    vf = (
        "format=yuv420p10le,"
        "zscale=p=bt709:t=bt709:m=bt709:r=tv:"
        "pin=bt709:tin=bt709:min=bt709:rin=tv,"
        "zscale=t=linear:npl=100,"
        "zscale=t=smpte2084:p=bt2020:m=bt2020nc:r=tv,"
        "format=yuv420p10le"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", "testsrc2=s=1280x720:r=24:d=2",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2",
        "-vf", vf,
        "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params",
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited",
        "-pix_fmt", "yuv420p10le",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


@needs_ffmpeg
@needs_tonemap_stack
@pytest.mark.integration
def test_hdr_to_sdr_produces_bt709_output(
    hdr_clip_for_tonemap: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    out = tmp_path / "sdr_out.mp4"
    profile = load_profile(PROFILES_DIR / "cid_aware_hdr_to_sdr.yaml")
    plan = build_plan(hdr_clip_for_tonemap, profile, encoder_override="libx264")

    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        target_segment_sec=600.0,
        enforce_preflight=False,  # short clip; tests audio.sr might warn
    )
    run_full(plan, options)
    assert out.exists()

    meta = probe(out)
    v = meta.video[0]
    # Output is plain BT.709 SDR.
    assert v.color.is_hdr is False
    assert v.color.transfer in ("bt709", "unknown")
    # 8-bit pix_fmt.
    assert "10" not in v.pix_fmt
