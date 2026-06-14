"""v1.0.1 Task 3: resume re-encodes a segment whose output was truncated.

Failure mode this guards: ffmpeg exits 0, the segment is marked
``done``, then a power loss / NFS hiccup / aggressive antivirus
truncates ``seg_NNNN.mkv`` to 0 bytes. Pre-v1.0.1 the resume path
trusted the ``done`` status and concat would fail with "Impossible to
open …" — or worse, succeed against a partial file and produce a
visibly broken final mp4. Post-v1.0.1 the on-resume verifier checks
each ``done`` segment's on-disk SHA-256 (and zero-byte fast-path) and
demotes it to ``pending`` so the next run re-encodes only the broken
segment.
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
    """5-second clip with forced 0.5 s keyframe interval (same shape as
    test_resume_partial_cleanup.multi_segment_clip)."""
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
def test_resume_reencodes_truncated_segment(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
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
            "ffmpeg GOP forcing produced <2 segments — truncated-resume "
            "scenario requires ≥2 to single out the corrupted one."
        )

    state_path = work_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    seg_records = [s for s in state["segments"] if s.get("out_path")]
    assert all(s["status"] == "done" for s in seg_records)
    # v1.0.1: the orchestrator must have recorded a sha256 per done segment.
    assert all(s.get("sha256") for s in seg_records), (
        "v1.0.1 orchestrator must populate Segment.sha256 on done"
    )

    # Truncate ONE middle segment to 0 bytes. Pre-fix this would survive
    # to concat and corrupt the final output (or crash with "Impossible
    # to open").
    out1.unlink()
    victim_idx = len(seg_records) // 2
    victim_path = Path(seg_records[victim_idx]["out_path"])
    assert victim_path.exists()
    victim_path.write_bytes(b"")

    # Second run — verifier should catch the zero-byte segment, demote
    # it to pending, and the encoder should re-do just that segment.
    out2 = tmp_path / "out2.mp4"
    options2 = RunOptions(
        work_dir=work_dir, output=out2, keep_segments=False,
        target_segment_sec=1.0,
    )
    summary2 = run_full(plan, options2)

    assert out2.exists() and out2.stat().st_size > 0
    assert summary2.segments_done == summary1.segments_done

    # The post-resume state must show a fresh, non-zero file for the
    # victim segment with a new sha256.
    state_after = json.loads((work_dir / "state.json").read_text(encoding="utf-8"))
    victim_record_after = next(
        s for s in state_after["segments"] if s["idx"] == seg_records[victim_idx]["idx"]
    )
    assert victim_record_after["status"] == "done"
    assert victim_record_after.get("sha256"), "re-encoded segment must repopulate sha256"
    assert victim_record_after["sha256"] != seg_records[victim_idx].get("sha256")
