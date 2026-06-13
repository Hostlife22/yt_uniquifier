"""v0.7.0 R3 / F3 — integration smoke for the 7 shipped platform profiles.

Each profile is loaded, planned, and run through the full
orchestrator on a tiny 2-second clip. Verifies that:

  * the profile YAML is valid (extra=forbid contract)
  * the new ``video.fit_aspect`` transform composes with every
    other transform in the shipped profiles (no double-prefix /
    unwrapped ``__IN__`` regressions)
  * the resulting output exists, is non-empty, and has the target
    aspect within ±2 px (rounding for even-dim guard)

Per-profile encode wall-time is small (~1-2 s on macOS m-series),
so all 7 profiles together stay well under a minute on CI even
with libx264 ultrafast.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.paths import profiles_dir

# Expected (W, H) per profile.  Drives the post-encode aspect-ratio assert
# and documents what each shipped profile promises to the user.
_PLATFORM_PROFILES: list[tuple[str, tuple[int, int]]] = [
    ("youtube_4k",       (3840, 2160)),
    ("youtube_1080p",    (1920, 1080)),
    ("youtube_shorts",   (1080, 1920)),
    ("tiktok_vertical",  (1080, 1920)),
    ("instagram_reels",  (1080, 1920)),
    ("instagram_square", (1080, 1080)),
    ("linkedin_square",  (1080, 1080)),
]


def _probe_resolution(path: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w_str, h_str = out.split("x")
    return int(w_str), int(h_str)


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("name,expected_wh", _PLATFORM_PROFILES)
def test_platform_profile_encodes_clean(
    name: str,
    expected_wh: tuple[int, int],
    tiny_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    """Each shipped platform profile encodes the tiny clip and lands
    at the target resolution (±2 px for the even-dim guard).
    """
    profile = load_profile(profiles_dir() / f"{name}.yaml")
    plan = build_plan(tiny_clip, profile, encoder_override="libx264")
    out = tmp_path / f"{name}.mp4"
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        keep_segments=False,
    )
    run_full(plan, options)

    assert out.exists(), f"{name}: orchestrator did not write output"
    assert out.stat().st_size > 0, f"{name}: output is empty"

    w, h = _probe_resolution(out)
    exp_w, exp_h = expected_wh
    # Allow ±2 px for the trunc(_/2)*2 even-dim guard at the chain tail.
    assert abs(w - exp_w) <= 2, f"{name}: width {w} != target {exp_w}"
    assert abs(h - exp_h) <= 2, f"{name}: height {h} != target {exp_h}"


def test_every_shipped_profile_has_integration_coverage() -> None:
    """Guard against shipping a profile that the parametrize table forgot.

    Future profiles must either be added to ``_PLATFORM_PROFILES`` or
    explicitly excluded here. Catches the "added a YAML, forgot the
    test row" failure mode.
    """
    covered = {name for name, _ in _PLATFORM_PROFILES}
    shipped = {
        p.stem for p in profiles_dir().glob("*.yaml")
        if p.stem.startswith(("youtube_", "tiktok_", "instagram_", "linkedin_"))
    }
    missing = shipped - covered
    assert not missing, (
        "shipped platform profile(s) missing from _PLATFORM_PROFILES: "
        f"{sorted(missing)}"
    )
