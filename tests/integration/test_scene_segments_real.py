"""v0.8.0 R3 — end-to-end scene-segmentation with real ffmpeg + PySceneDetect.

We generate a multi-cut source on the fly via ``ffmpeg concat`` (three
visually distinct ``testsrc2`` clips with different ``rate`` / size /
color setups). The scene detector should find boundaries between them;
the segmenter must produce more than one segment AND every emitted
``start_sec`` / ``end_sec`` must coincide with a real keyframe so
``stream_copy_extract`` would succeed.

Skipped when either ffmpeg or ``scenedetect`` is missing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg

scenedetect = pytest.importorskip("scenedetect")  # noqa: F841


def _make_three_scene_clip(out: Path) -> None:
    """Concatenate three visually distinct testsrc2 clips into one file."""
    parts = []
    setups = [
        ("testsrc2=size=320x180:rate=24:duration=1", "sine=frequency=220:duration=1"),
        ("color=c=red:size=320x180:rate=24:duration=1", "sine=frequency=440:duration=1"),
        ("color=c=blue:size=320x180:rate=24:duration=1", "sine=frequency=880:duration=1"),
    ]
    for i, (v, a) in enumerate(setups):
        part = out.parent / f"part_{i}.mp4"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", v,
            "-f", "lavfi", "-i", a,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "24",  # one keyframe per second so the snap has options
            "-c:a", "aac", "-shortest", str(part),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        parts.append(part)
    concat_list = out.parent / "list.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", str(out),
        ],
        check=True, capture_output=True, timeout=30,
    )


@needs_ffmpeg
@pytest.mark.integration
def test_scene_mode_produces_more_segments_than_keyframe_mode(tmp_path: Path) -> None:
    from yt_uniquifier.core import segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "three_scenes.mp4"
    _make_three_scene_clip(src)

    keyframes = segmenter.list_keyframes(src)
    assert keyframes, "test source should have detectable keyframes"

    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    source = SourceMeta(
        path=src,
        container="mp4",
        duration_sec=3.0,
        size_bytes=src.stat().st_size,
    )

    # Keyframe mode with default target_size_sec=600 collapses our 3 s
    # source to a single segment.
    kf_plan = Plan(
        source=source,
        profile=Profile(name="kf"),
        encoder=encoder,
        plan_hash="hk",
    )
    kf_segments = segmenter.plan_segments(kf_plan)
    assert len(kf_segments) == 1, "3 s source under target_size 600 s = one segment"

    # Scene mode with a low min-length and the library default threshold
    # should split between the visually distinct testsrc2 / red / blue
    # parts.
    scene_plan = Plan(
        source=source,
        profile=Profile(
            name="scene",
            segmentation=SegmentationConfig(
                mode="scene",
                scene_threshold=27.0,
                scene_min_length_sec=0.5,  # tiny clip → allow short scenes
            ),
        ),
        encoder=encoder,
        plan_hash="hs",
    )
    scene_segments = segmenter.plan_segments(scene_plan)
    assert len(scene_segments) >= 2, (
        f"scene mode should split the 3-scene source; got {len(scene_segments)} "
        f"segments at {[(s.start_sec, s.end_sec) for s in scene_segments]}"
    )

    # Stream-copy invariant: every internal boundary must be a real
    # keyframe. (The last segment's end is duration, which the planner
    # appends explicitly — no need to be a keyframe.)
    kf_tol = 1e-3
    for s in scene_segments[1:]:
        assert any(abs(s.start_sec - kf) < kf_tol for kf in keyframes), (
            f"segment {s.idx} starts at {s.start_sec} — not on any keyframe "
            f"({keyframes})"
        )


@needs_ffmpeg
@pytest.mark.integration
def test_scene_mode_is_deterministic_across_calls(tmp_path: Path) -> None:
    """Resume-safety regression: scene-detect must produce the same
    segments for the same source bytes on two separate ``plan_segments``
    calls (the keyframe cache + PySceneDetect determinism cover this,
    but a regression here would silently break resume across versions)."""
    from yt_uniquifier.core import segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "three_scenes.mp4"
    _make_three_scene_clip(src)

    plan = Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=3.0,
            size_bytes=src.stat().st_size,
        ),
        profile=Profile(
            name="scene",
            segmentation=SegmentationConfig(
                mode="scene", scene_min_length_sec=0.5,
            ),
        ),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="hs",
    )
    first = [(s.start_sec, s.end_sec) for s in segmenter.plan_segments(plan)]
    second = [(s.start_sec, s.end_sec) for s in segmenter.plan_segments(plan)]
    assert first == second, (
        f"scene segmentation must be deterministic: {first} != {second}"
    )
