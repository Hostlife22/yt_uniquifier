"""Real-ffmpeg integration: a synthetic HDR clip survives the full pipeline."""

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


def _have_libx265() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return "libx265" in res.stdout


def _have_zscale() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return "zscale" in res.stdout


needs_hdr_stack = pytest.mark.skipif(
    not (_have_libx265() and _have_zscale()),
    reason="HDR pipeline needs ffmpeg with libx265 + zscale (zimg)",
)


@pytest.fixture(scope="session")
def hdr_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic 2-second 1280x720 clip tagged as PQ/BT.2020."""
    out = tmp_path_factory.mktemp("hdr") / "hdr.mp4"
    # zscale needs explicit input color tags because testsrc2 doesn't carry them.
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
        "-c:v", "libx265",
        "-preset", "ultrafast",
        "-x265-params",
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited",
        "-pix_fmt", "yuv420p10le",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Some ffmpeg/zimg combinations (e.g. ubuntu CI's older libzimg or
        # libx265 versions) can't run this elaborate BT.709→linear→PQ chain.
        # Treat fixture generation failure as a precondition miss, not an
        # error, so the dependent tests skip cleanly.
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
            else str(exc)
        )
        pytest.skip(f"HDR fixture generation failed on this ffmpeg/zimg: {stderr.strip()[-300:]}")
    return out


@needs_ffmpeg
@needs_hdr_stack
@pytest.mark.integration
def test_hdr_source_is_detected_as_hdr(hdr_clip: Path) -> None:
    meta = probe(hdr_clip)
    assert meta.video[0].color.is_hdr is True
    assert meta.video[0].color.transfer == "smpte2084"
    assert meta.video[0].color.primaries == "bt2020"


@needs_ffmpeg
@needs_hdr_stack
@pytest.mark.integration
def test_hdr_roundtrip_preserves_metadata(
    hdr_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    out = tmp_path / "hdr_out.mp4"
    profile = load_profile(PROFILES_DIR / "medium_hdr.yaml")
    plan = build_plan(hdr_clip, profile, encoder_override="libx265")

    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        target_segment_sec=600.0,
        enforce_preflight=True,
    )
    run_full(plan, options)
    assert out.exists()

    out_meta = probe(out)
    assert out_meta.video[0].color.is_hdr is True
    assert out_meta.video[0].color.transfer == "smpte2084"
    assert out_meta.video[0].color.primaries == "bt2020"
    # Output must remain 10-bit.
    assert "10" in out_meta.video[0].pix_fmt
