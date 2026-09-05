"""Real-ffmpeg integration: a synthetic HDR clip survives the full pipeline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.errors import PreflightFailure
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa import ssim
from yt_uniquifier.core.qa.registration import build_transformed_reference

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
        (
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited:"
            "master-display=G(8500,39850)B(6550,2300)R(35400,14600)"
            "WP(15635,16450)L(10000000,1):max-cll=1000,400"
        ),
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
    assert meta.video[0].color.mastering_display is not None
    assert meta.video[0].color.max_cll == 1000
    assert meta.video[0].color.max_fall == 400


@needs_ffmpeg
@needs_hdr_stack
@pytest.mark.integration
def test_hdr_without_explicit_output_policy_fails_before_segment_creation(
    hdr_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    output = tmp_path / "unsafe.mp4"
    work_dir = tmp_path / "work"
    profile = Profile(
        name="undefined-hdr-policy",
        transforms=[],
        keep_hdr=False,
        output_container="mp4",
        target_codec="h264",
    )
    plan = build_plan(hdr_clip, profile, encoder_override="libx264")

    with pytest.raises(PreflightFailure, match="hdr.output_policy.missing"):
        run_full(
            plan,
            RunOptions(
                work_dir=work_dir,
                output=output,
                target_segment_sec=600.0,
                enforce_preflight=True,
            ),
        )

    assert not output.exists()
    assert not (work_dir / "state.json").exists()


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
    assert out_meta.video[0].color.mastering_display == (
        "G(8500,39850)B(6550,2300)R(35400,14600)"
        "WP(15635,16450)L(10000000,1)"
    )
    assert out_meta.video[0].color.max_cll == 1000
    assert out_meta.video[0].color.max_fall == 400
    # Output must remain 10-bit.
    assert "10" in out_meta.video[0].pix_fmt
    assert out_meta.video[0].color.bit_depth == 10

    # The lossy HEVC output and the independently rendered lossless reference
    # must agree closely.  This catches pixel-format negotiation regressions:
    # a transfer-only PQ roundtrip in subsampled YUV previously kept every HDR
    # metadata field correct while producing severe green/orange highlights.
    reference = build_transformed_reference(
        plan,
        tmp_path / "registered" / "reference.mkv",
        target_segment_sec=600.0,
    )
    registered = ssim.compute(reference.path, out, reset_pts=True)
    assert registered.score is not None, registered.note
    assert registered.score > 0.95


@pytest.fixture(scope="session")
def hlg_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic 2-second HEVC clip tagged as HLG/BT.2020."""
    out = tmp_path_factory.mktemp("hlg") / "hlg.mp4"
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p10le",
        "-x265-params",
        "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:range=limited",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
        "-colorspace", "bt2020nc", "-color_range", "tv",
        "-c:a", "aac", "-shortest", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"HLG fixture generation failed: {exc}")
    return out


@needs_ffmpeg
@needs_hdr_stack
@pytest.mark.integration
def test_hlg_roundtrip_preserves_transfer_depth_and_timeline(
    hlg_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    out = tmp_path / "hlg_out.mp4"
    profile = load_profile(PROFILES_DIR / "medium_hdr.yaml")
    plan = build_plan(hlg_clip, profile, encoder_override="libx265")
    run_full(
        plan,
        RunOptions(
            work_dir=tmp_path / "work" / plan.plan_hash,
            output=out,
            target_segment_sec=600.0,
            enforce_preflight=True,
        ),
    )

    source_meta = probe(hlg_clip)
    out_meta = probe(out)
    video = out_meta.video[0]
    assert video.color.is_hdr is True
    assert video.color.transfer == "arib-std-b67"
    assert video.color.primaries == "bt2020"
    assert video.color.space == "bt2020nc"
    assert video.color.color_range == "tv"
    assert video.color.bit_depth == 10
    assert video.pix_fmt == "yuv420p10le"
    assert video.duration_sec == pytest.approx(source_meta.video[0].duration_sec, abs=0.05)
    assert out_meta.audio[0].sample_rate == 48_000
