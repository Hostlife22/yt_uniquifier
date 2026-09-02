"""B3 (v0.6.0) unit tests: fused single-fork segment command shape.

The fused builder replaces the two-fork ``stream_copy_extract +
build_video_segment_command`` pattern with a single ffmpeg
invocation that uses ``-ss/-t`` input seek on the source. This test
pins the command shape so a future change can't silently drop the
PTS handling that is load-bearing for concat correctness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.pipeline import build_video_segment_command_fused
from yt_uniquifier.core.segmenter import _fuse_enabled


def _plan(tmp_path: Path) -> Plan:
    src = tmp_path / "src.mp4"
    src.touch()
    return Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=120.0, size_bytes=1_000_000,
            video=[VideoStream(
                index=0, codec="h264", width=1920, height=1080, fps=24.0,
                duration_sec=120.0, pix_fmt="yuv420p",
                color=HDRInfo(is_hdr=False),
            )],
            audio=[AudioStream(
                index=1, codec="aac", sample_rate=48000, channels=2,
            )],
        ),
        profile=Profile(name="t"),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="abcd1234" * 2,
        run_seed=42,
    )


def test_fused_command_has_input_seek_and_duration_clamp(tmp_path: Path) -> None:
    """B3: ``-ss`` must come BEFORE ``-i`` (input seek, keyframe-
    aligned, cheap). ``-t`` must come after ``-i`` to clamp duration.
    """
    plan = _plan(tmp_path)
    segment = Segment(idx=3, start_sec=30.0, end_sec=42.0, status="pending")
    out = tmp_path / "seg_0003.mkv"

    cmd = build_video_segment_command_fused(
        plan, segment, plan.source.path, out,
    )
    args = cmd.args

    # Locate input/output anchors.
    i_idx = args.index("-i")
    ss_idx = args.index("-ss")
    t_idx = args.index("-t")

    # -ss must be BEFORE -i (input seek). Output seek would be slow.
    assert ss_idx < i_idx, (
        "fused builder placed -ss after -i — that triggers slow output "
        "seek instead of keyframe-aligned input seek"
    )
    assert args[ss_idx + 1] == f"{segment.start_sec:.6f}"

    # -t must be AFTER -i (constrains output duration).
    assert t_idx > i_idx
    span = segment.end_sec - segment.start_sec
    assert args[t_idx + 1] == f"{span:.6f}"


def test_fused_command_rebases_video_pts_without_global_timestamp_shift(
    tmp_path: Path,
) -> None:
    """Decoded video is rebased in-filter; global timestamp shifting is unsafe
    when copied AAC packets have negative priming timestamps.
    """
    plan = _plan(tmp_path)
    segment = Segment(idx=0, start_sec=0.0, end_sec=10.0, status="pending")
    out = tmp_path / "seg_0000.mkv"

    cmd = build_video_segment_command_fused(
        plan, segment, plan.source.path, out,
    )
    args = cmd.args

    assert "-avoid_negative_ts" not in args
    graph = args[args.index("-filter_complex") + 1]
    assert "setpts=PTS-STARTPTS" in graph


def test_fused_command_outputs_video_only(tmp_path: Path) -> None:
    """Audio/subtitles are mapped from the source exactly once at final mux."""
    plan = _plan(tmp_path)
    segment = Segment(idx=1, start_sec=10.0, end_sec=20.0, status="pending")
    out = tmp_path / "seg_0001.mkv"

    cmd = build_video_segment_command_fused(
        plan, segment, plan.source.path, out,
    )
    args = cmd.args

    assert "-an" in args
    assert "-sn" in args
    assert "0:a?" not in args
    assert "0:s?" not in args


def test_fused_command_includes_filter_complex(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    segment = Segment(idx=2, start_sec=20.0, end_sec=30.0, status="pending")
    out = tmp_path / "seg_0002.mkv"

    cmd = build_video_segment_command_fused(
        plan, segment, plan.source.path, out,
    )
    args = cmd.args

    fc_idx = args.index("-filter_complex")
    fc_value = args[fc_idx + 1]
    # The trailing even-dim guard is always present after the user
    # transform chain.
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fc_value
    assert "format=yuv420p" in fc_value
    assert "setpts=PTS-STARTPTS" in fc_value


def test_fused_command_outputs_to_segment_path(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    segment = Segment(idx=5, start_sec=50.0, end_sec=60.0, status="pending")
    out = tmp_path / "seg_0005.mkv"

    cmd = build_video_segment_command_fused(
        plan, segment, plan.source.path, out,
    )
    assert cmd.args[-1] == str(out)


# -------------------------------------------------------- opt-out


def test_fuse_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env var, fuse is on by default."""
    monkeypatch.delenv("YT_UNIQ_DISABLE_FUSE", raising=False)
    assert _fuse_enabled() is True


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", " YES "])
def test_fuse_disabled_via_env(val: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var accepts common truthy spellings."""
    monkeypatch.setenv("YT_UNIQ_DISABLE_FUSE", val)
    assert _fuse_enabled() is False


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "random_garbage"])
def test_fuse_remains_enabled_for_non_truthy_env(
    val: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YT_UNIQ_DISABLE_FUSE", val)
    assert _fuse_enabled() is True
