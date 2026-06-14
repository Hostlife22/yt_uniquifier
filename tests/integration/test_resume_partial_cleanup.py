"""A3 (v0.5.5) regression: resume recovers from partial segment cleanup.

Pre-fix bug: if state.json reports every segment as ``done`` but only
SOME segment output files survive (NFS partial sync, interrupted cleanup,
manual deletion of a corrupted segment), the orchestrator's reset-to-
pending branch was guarded by ``not any(... .exists())`` — i.e. it only
fired when ALL segment files were gone. Partial cleanup state passed
through to concat which then failed with "Impossible to open seg_NNNN.mkv".

Post-fix: per-segment recovery. Each segment whose ``out_path`` is
missing on disk is re-marked ``pending`` and re-encoded; segments whose
files survived are reused. Concat additionally filters by ``.exists()``
as defence-in-depth.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@pytest.fixture
def multi_segment_clip(tmp_path: Path) -> Path:
    """5-second clip with forced 0.5 s keyframe interval.

    plan_segments cuts at keyframes; tiny_clip's default GOP is too
    coarse to produce multiple segments at the orchestrator's
    target_segment_sec floor (1.0 s). Forcing keyint=12 at 24 fps gives
    a keyframe every 0.5 s, so plan_segments(target=1.0) on a 5 s clip
    produces ~5 segments — enough to test partial-cleanup recovery.
    """
    out = tmp_path / "multi.mp4"
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


@needs_ffmpeg
@pytest.mark.integration
def test_resume_recovers_when_all_segments_missing(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """Original `not any(...exists())` branch still works after the fix.

    State reports done, every seg file is deleted, output is gone →
    recovery marks all segments pending and re-encodes from scratch.
    """
    out1 = tmp_path / "out1.mp4"
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(tiny_clip, profile, encoder_override="libx264")
    work_dir = tmp_path / "work" / plan.plan_hash

    # First run — keep_segments=True so we can later inspect / delete.
    options1 = RunOptions(
        work_dir=work_dir, output=out1, keep_segments=True,
        target_segment_sec=600.0,
    )
    run_full(plan, options1)

    state_path = work_dir / "state.json"
    state = json.loads(state_path.read_text())
    seg_records = [s for s in state["segments"] if s.get("out_path")]
    assert seg_records, "test setup invariant: state.json must record out_paths"

    # Delete output AND every segment file (simulating full cleanup
    # followed by output deletion).
    out1.unlink()
    for s in seg_records:
        Path(s["out_path"]).unlink(missing_ok=True)

    # Second run — should recover.
    out2 = tmp_path / "out2.mp4"
    options2 = RunOptions(
        work_dir=work_dir, output=out2, keep_segments=False,
        target_segment_sec=600.0,
    )
    summary = run_full(plan, options2)

    assert out2.exists() and out2.stat().st_size > 0
    assert summary.segments_done >= 1


@needs_ffmpeg
@pytest.mark.integration
def test_resume_recovers_when_some_segments_missing(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """A3 core regression: PARTIAL cleanup must re-encode only the gaps.

    A middle segment file is deleted; the surviving segments must be
    reused and the missing one re-encoded. Pre-fix this scenario would
    crash in concat with "Impossible to open seg_NNNN.mkv".
    """
    out1 = tmp_path / "out1.mp4"
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(multi_segment_clip, profile, encoder_override="libx264")
    work_dir = tmp_path / "work" / plan.plan_hash

    options1 = RunOptions(
        work_dir=work_dir, output=out1, keep_segments=True,
        target_segment_sec=1.0,
    )
    summary1 = run_full(plan, options1)

    if summary1.segments_done < 2:
        pytest.skip(
            "ffmpeg GOP forcing did not produce ≥2 segments — "
            "partial-cleanup scenario requires ≥2 to exercise. The "
            "all-missing case is covered by the sibling test."
        )

    state_path = work_dir / "state.json"
    state = json.loads(state_path.read_text())
    seg_records = [s for s in state["segments"] if s.get("out_path")]
    assert len(seg_records) >= 2

    # Delete the output AND a single segment file in the middle —
    # leaving a true "partial" state. Pre-fix this is the path that
    # crashed concat.
    out1.unlink()
    victim = Path(seg_records[len(seg_records) // 2]["out_path"])
    assert victim.exists(), "victim segment file should exist before delete"
    victim.unlink()

    # Second run.
    out2 = tmp_path / "out2.mp4"
    options2 = RunOptions(
        work_dir=work_dir, output=out2, keep_segments=False,
        target_segment_sec=1.0,
    )
    summary2 = run_full(plan, options2)

    assert out2.exists() and out2.stat().st_size > 0
    assert summary2.segments_done == summary1.segments_done, (
        "all segments must be present in the final concat"
    )
