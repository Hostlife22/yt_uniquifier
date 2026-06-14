"""B3 (v0.6.0) integration: fused single-fork segment encode produces
output equivalent to the legacy two-fork extract+re-encode path.

This is the critical regression test for B3: the fused builder uses
``-ss <start> -i source -t <span>`` input seek + ``-avoid_negative_ts
make_zero`` instead of pre-extracting each segment to a ``_src.mkv``
intermediate. Any silent break in PTS handling here would manifest
as audio/video desync at segment boundaries in production.

Strategy: run the full orchestrator on a multi-segment clip BOTH
with fuse enabled (default) and with ``YT_UNIQ_DISABLE_FUSE=1`` and
assert that both outputs:
  - exist and are non-empty
  - have duration within 0.5 s of the source (the concat-step `-t`
    trim contract from CRIT-2)
  - have one video + one audio stream
  - share the same number of segments per state.json
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@pytest.fixture
def multi_segment_clip(tmp_path: Path) -> Path:
    """5-second clip with forced 0.5 s keyframe interval. Mirrors the
    fixture in test_resume_partial_cleanup.py so both tests share the
    same generation path.
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
def test_fused_path_produces_correct_output(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: default fused path must yield a valid output that matches
    source duration within the concat ``-t`` trim window.
    """
    monkeypatch.delenv("YT_UNIQ_DISABLE_FUSE", raising=False)

    out = tmp_path / "fused.mp4"
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(multi_segment_clip, profile, encoder_override="libx264")
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        keep_segments=False,
        target_segment_sec=1.0,  # Force ≥2 segments to exercise concat seams.
    )
    summary = run_full(plan, options)

    assert summary.segments_done >= 2, (
        "test needs ≥2 segments to exercise the concat seam — got "
        f"{summary.segments_done}. Tighten target_segment_sec or the "
        "GOP forcing in the multi_segment_clip fixture."
    )
    assert out.exists() and out.stat().st_size > 0

    src_meta = probe(multi_segment_clip)
    out_meta = probe(out)

    # Duration within 0.5 s of source (concat -t contract).
    duration_delta = abs(out_meta.duration_sec - src_meta.duration_sec)
    assert duration_delta < 0.5, (
        f"output duration {out_meta.duration_sec:.3f} drifted by "
        f"{duration_delta:.3f} s from source {src_meta.duration_sec:.3f}"
    )

    assert len(out_meta.video) == 1
    assert len(out_meta.audio) == 1
    assert out_meta.audio[0].codec == "aac"


@needs_ffmpeg
@pytest.mark.integration
def test_legacy_two_fork_path_still_works(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: emergency rollback path. With ``YT_UNIQ_DISABLE_FUSE=1`` the
    legacy two-fork ``stream_copy_extract + build_video_segment_command``
    pattern remains available and must produce equivalent output.
    """
    monkeypatch.setenv("YT_UNIQ_DISABLE_FUSE", "1")

    out = tmp_path / "legacy.mp4"
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(multi_segment_clip, profile, encoder_override="libx264")
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        keep_segments=True,  # keep so we can verify _src.mkv was created.
        target_segment_sec=1.0,
    )
    summary = run_full(plan, options)

    assert out.exists() and out.stat().st_size > 0
    assert summary.segments_done >= 2

    # The legacy path produces seg_NNNN_src.mkv intermediates.
    src_files = sorted((options.work_dir).glob("seg_*_src.mkv"))
    assert src_files, (
        "legacy two-fork path must produce seg_*_src.mkv intermediates; "
        "found none — opt-out is not taking effect"
    )


@needs_ffmpeg
@pytest.mark.integration
def test_fused_path_skips_src_mkv_intermediate(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3 core perf win: the fused path must NOT create ``seg_NNNN_src.mkv``
    files. Pre-fix every segment cost ~600 MB of peak disk for the
    stream-copy intermediate; eliminating it is the whole point.
    """
    monkeypatch.delenv("YT_UNIQ_DISABLE_FUSE", raising=False)

    out = tmp_path / "fused.mp4"
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(multi_segment_clip, profile, encoder_override="libx264")
    options = RunOptions(
        work_dir=tmp_path / "work" / plan.plan_hash,
        output=out,
        keep_segments=True,  # keep so we can verify no _src.mkv survives.
        target_segment_sec=1.0,
    )
    run_full(plan, options)

    src_files = list((options.work_dir).glob("seg_*_src.mkv"))
    assert not src_files, (
        f"fused path produced {len(src_files)} seg_*_src.mkv intermediate(s) "
        "— peak disk regression. The fused builder should read directly "
        "from the source via -ss/-t input seek."
    )

    # And the state.json src_path field should be null for every segment
    # (the fused path passes src=None to CheckpointStore.mark).
    state = json.loads((options.work_dir / "state.json").read_text())
    for seg in state["segments"]:
        # ``src_path`` is omitted entirely on the fused path because
        # store.mark() only writes the field when the value is not None.
        assert "src_path" not in seg or seg["src_path"] in (None, ""), (
            f"segment {seg['idx']} still has src_path set under fused mode: "
            f"{seg.get('src_path')!r}"
        )


@needs_ffmpeg
@pytest.mark.integration
def test_fused_and_legacy_have_matching_segment_count(
    multi_segment_clip: Path, tmp_path: Path, isolated_cache: Path,
) -> None:
    """B3 equivalence: identical (source, profile, target_segment_sec)
    must produce the same segment plan regardless of execution path.
    Catches a class of bugs where the fused path silently changes
    plan_segments behaviour via a different code path.
    """
    profile = load_profile(PROFILES_DIR / "soft.yaml")

    # Fused run.
    os.environ.pop("YT_UNIQ_DISABLE_FUSE", None)
    out_fused = tmp_path / "fused.mp4"
    plan_a = build_plan(
        multi_segment_clip, profile, encoder_override="libx264",
    )
    run_full(plan_a, RunOptions(
        work_dir=tmp_path / "work_fused" / plan_a.plan_hash,
        output=out_fused, keep_segments=False,
        target_segment_sec=1.0,
    ))

    # Legacy run.
    os.environ["YT_UNIQ_DISABLE_FUSE"] = "1"
    try:
        out_legacy = tmp_path / "legacy.mp4"
        plan_b = build_plan(
            multi_segment_clip, profile, encoder_override="libx264",
        )
        run_full(plan_b, RunOptions(
            work_dir=tmp_path / "work_legacy" / plan_b.plan_hash,
            output=out_legacy, keep_segments=False,
            target_segment_sec=1.0,
        ))
    finally:
        os.environ.pop("YT_UNIQ_DISABLE_FUSE", None)

    fused_state = json.loads(
        (tmp_path / "work_fused" / plan_a.plan_hash / "state.json").read_text()
    )
    legacy_state = json.loads(
        (tmp_path / "work_legacy" / plan_b.plan_hash / "state.json").read_text()
    )
    assert len(fused_state["segments"]) == len(legacy_state["segments"]), (
        "segment counts diverge between fused and legacy paths — "
        "plan_segments invariants broken"
    )

    # Both outputs muxed within the same duration envelope.
    fused_dur = probe(out_fused).duration_sec
    legacy_dur = probe(out_legacy).duration_sec
    assert abs(fused_dur - legacy_dur) < 0.2, (
        f"fused ({fused_dur:.3f}s) and legacy ({legacy_dur:.3f}s) "
        "outputs drift > 0.2 s — concat seam regression"
    )
