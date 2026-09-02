"""v0.8.0 R3 — scene-detect adapter + snap-to-keyframe planner.

The snap logic is the load-bearing piece (stream_copy_extract requires
every cut to land on a keyframe). It's pure-Python and testable without
the optional ``scenedetect`` dependency. Tests that exercise the
PySceneDetect adapter itself use ``pytest.importorskip``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.scene_detect import (
    detect_scene_boundaries,
    snap_to_keyframes,
)

# ---------------------------------------------------------------------------
# snap_to_keyframes — pure logic, runs without PySceneDetect
# ---------------------------------------------------------------------------


def test_snap_rounds_down_to_keyframe() -> None:
    """A boundary between two keyframes must snap to the EARLIER one
    so the preceding segment includes its trailing-keyframe-interval
    frames — otherwise stream_copy_extract loses them."""
    keyframes = [0.0, 5.0, 10.0, 15.0, 20.0]
    # Cut at 12.3s — between kf=10 and kf=15. Must snap to 10.
    assert snap_to_keyframes([12.3], keyframes) == [10.0]


def test_snap_collapses_multiple_cuts_in_one_keyframe_interval() -> None:
    """Three cuts inside [10, 15) → one boundary at 10."""
    keyframes = [0.0, 5.0, 10.0, 15.0, 20.0]
    cuts = [11.0, 12.0, 13.5]
    assert snap_to_keyframes(cuts, keyframes) == [10.0]


def test_snap_drops_zero_boundary() -> None:
    """The planner adds 0.0 implicitly; the snap pass must not duplicate it."""
    keyframes = [0.0, 5.0, 10.0]
    # A cut at 4.9s snaps to 0.0 — that's the implicit start, drop it.
    assert snap_to_keyframes([4.9], keyframes) == []


def test_snap_enforces_min_length_between_kept_boundaries() -> None:
    """If snapping pulls two distinct cuts within min_length of each
    other, the second is dropped (the user asked for at-least-N-sec
    segments and the keyframe spacing pulled them too close)."""
    keyframes = [0.0, 5.0, 5.5, 10.0]  # kf 5.0 and 5.5 are very close
    cuts = [5.1, 5.6]                  # would snap to 5.0 and 5.5
    # min_length_sec=1.0 keeps 5.0, drops 5.5 (distance 0.5 < 1.0).
    assert snap_to_keyframes(cuts, keyframes, min_length_sec=1.0) == [5.0]


def test_snap_deduplicates_exact_duplicates() -> None:
    keyframes = [0.0, 5.0, 10.0]
    # Two cuts that snap to the same kf — collapse.
    assert snap_to_keyframes([6.0, 7.0, 8.0], keyframes) == [5.0]


def test_snap_returns_empty_when_no_inputs() -> None:
    assert snap_to_keyframes([], [0.0, 5.0]) == []
    assert snap_to_keyframes([1.0, 2.0], []) == []


def test_snap_ignores_boundary_before_first_keyframe() -> None:
    """If the source keyframe list happens to start at >0 (unusual but
    legal) and a scene cut lands earlier, we have no keyframe to snap
    to — drop the cut rather than fabricate a 0 we know isn't a real
    keyframe in the source."""
    keyframes = [10.0, 20.0, 30.0]
    assert snap_to_keyframes([5.0, 25.0], keyframes) == [20.0]


def test_snap_preserves_ordering_with_unsorted_input() -> None:
    keyframes = [0.0, 5.0, 10.0, 15.0, 20.0]
    cuts = [16.0, 6.0, 11.0]
    assert snap_to_keyframes(cuts, keyframes) == [5.0, 10.0, 15.0]


# ---------------------------------------------------------------------------
# detect_scene_boundaries — adapter contract
# ---------------------------------------------------------------------------


def test_detect_raises_pipeline_error_when_dep_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lazy import must surface as a PipelineError with the install
    hint, NOT a bare ImportError. Bare ImportError reads as
    "yt-uniquifier is broken" rather than "this profile needs an extra"."""
    # Force the lazy ``from scenedetect import …`` line to fail.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **kw: object) -> object:
        if name == "scenedetect":
            raise ImportError("No module named 'scenedetect'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    src = tmp_path / "movie.mp4"
    src.touch()
    with pytest.raises(PipelineError, match="install yt-uniquifier\\[scene\\]"):
        detect_scene_boundaries(src)


def test_detect_raises_when_source_missing(tmp_path: Path) -> None:
    pytest.importorskip("scenedetect")
    with pytest.raises(PipelineError, match="does not exist"):
        detect_scene_boundaries(tmp_path / "no-such.mp4")


# ---------------------------------------------------------------------------
# plan_segments dispatch (no real ffmpeg required — monkey-patch helpers)
# ---------------------------------------------------------------------------


def test_plan_segments_scene_mode_uses_snapped_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end planner: scene mode pulls boundaries from the
    scene-detect adapter, snaps them, and emits Segments that the
    rest of the pipeline can stream-copy from."""
    from yt_uniquifier.core import scene_detect, segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    fake_keyframes = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    fake_scene_cuts = [7.4, 13.0, 21.5]  # snap to 5, 10, 20

    src = tmp_path / "movie.mp4"
    src.touch()

    monkeypatch.setattr(segmenter, "list_keyframes", lambda _p: fake_keyframes)
    monkeypatch.setattr(
        scene_detect,
        "detect_scene_boundaries",
        lambda _p, *, threshold, min_length_sec: fake_scene_cuts,
    )

    profile = Profile(
        name="scene-test",
        segmentation=SegmentationConfig(mode="scene", scene_min_length_sec=1.0),
    )
    source = SourceMeta(
        path=src, container="mp4", duration_sec=30.0, size_bytes=1,
    )
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(source=source, profile=profile, encoder=encoder, plan_hash="x")

    segments = segmenter.plan_segments(plan)
    # 3 cuts at [5, 10, 20] plus duration=30 → 4 segments:
    # [0..5], [5..10], [10..20], [20..30]
    assert [(s.start_sec, s.end_sec) for s in segments] == [
        (0.0, 5.0),
        (5.0, 10.0),
        (10.0, 20.0),
        (20.0, 30.0),
    ]
    # idx is contiguous from 0.
    assert [s.idx for s in segments] == [0, 1, 2, 3]


def test_plan_segments_scene_mode_no_cuts_returns_single_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clip with no detected cuts (or all collapsed) falls back to one
    whole-source segment — matches keyframe-mode short-input behaviour."""
    from yt_uniquifier.core import scene_detect, segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "movie.mp4"
    src.touch()
    monkeypatch.setattr(segmenter, "list_keyframes", lambda _p: [0.0, 5.0, 10.0])
    monkeypatch.setattr(
        scene_detect,
        "detect_scene_boundaries",
        lambda _p, *, threshold, min_length_sec: [],
    )

    profile = Profile(name="scene-test", segmentation=SegmentationConfig(mode="scene"))
    source = SourceMeta(path=src, container="mp4", duration_sec=10.0, size_bytes=1)
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(source=source, profile=profile, encoder=encoder, plan_hash="x")

    segments = segmenter.plan_segments(plan)
    assert len(segments) == 1
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec == 10.0


def test_scene_mode_bounds_static_long_source_by_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No scene cuts must not turn a feature-length run into one huge segment."""
    from yt_uniquifier.core import scene_detect, segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "static.mp4"
    src.touch()
    keyframes = [float(second) for second in range(0, 1801, 100)]
    monkeypatch.setattr(segmenter, "list_keyframes", lambda _p: keyframes)
    monkeypatch.setattr(
        scene_detect,
        "detect_scene_boundaries",
        lambda _p, *, threshold, min_length_sec: [],
    )
    plan = Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=1800.0, size_bytes=1,
        ),
        profile=Profile(
            name="scene-static",
            segmentation=SegmentationConfig(mode="scene"),
        ),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="static",
    )

    segments = segmenter.plan_segments(plan, target_size_sec=600.0)

    assert [(s.start_sec, s.end_sec) for s in segments] == [
        (0.0, 600.0),
        (600.0, 1200.0),
        (1200.0, 1800.0),
    ]


def test_scene_mode_splits_long_gap_after_last_scene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sparse early scene cut must not leave an unbounded final segment."""
    from yt_uniquifier.core import scene_detect, segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "sparse-scenes.mp4"
    src.touch()
    keyframes = [float(second) for second in range(0, 1801, 100)]
    monkeypatch.setattr(segmenter, "list_keyframes", lambda _p: keyframes)
    monkeypatch.setattr(
        scene_detect,
        "detect_scene_boundaries",
        lambda _p, *, threshold, min_length_sec: [100.0],
    )
    plan = Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=1800.0, size_bytes=1,
        ),
        profile=Profile(
            name="scene-sparse",
            segmentation=SegmentationConfig(mode="scene"),
        ),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="sparse",
    )

    segments = segmenter.plan_segments(plan, target_size_sec=600.0)

    assert segments[0].end_sec == 100.0
    assert max(s.end_sec - s.start_sec for s in segments) <= 600.0


def test_scene_mode_drops_tiny_leading_and_trailing_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yt_uniquifier.core import scene_detect, segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SegmentationConfig,
        SourceMeta,
    )

    src = tmp_path / "edge-cuts.mp4"
    src.touch()
    keyframes = [0.0, 0.5, 2.0, 4.0, 6.0, 8.0, 9.5, 10.0]
    monkeypatch.setattr(segmenter, "list_keyframes", lambda _p: keyframes)
    monkeypatch.setattr(
        scene_detect,
        "detect_scene_boundaries",
        lambda _p, *, threshold, min_length_sec: [0.5, 9.5],
    )
    plan = Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=10.0, size_bytes=1,
        ),
        profile=Profile(
            name="scene-edges",
            segmentation=SegmentationConfig(
                mode="scene", scene_min_length_sec=2.0,
            ),
        ),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="edges",
    )

    segments = segmenter.plan_segments(plan, target_size_sec=600.0)

    assert [(s.start_sec, s.end_sec) for s in segments] == [(0.0, 10.0)]


def test_plan_segments_keyframe_mode_unchanged_by_segmentation_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Profiles without an explicit ``segmentation`` block must keep
    using keyframe mode. Regression guard for the default value of
    ``Profile.segmentation``."""
    from yt_uniquifier.core import segmenter
    from yt_uniquifier.core.models import (
        EncoderCandidate,
        Plan,
        Profile,
        SourceMeta,
    )

    src = tmp_path / "movie.mp4"
    src.touch()
    monkeypatch.setattr(
        segmenter, "list_keyframes",
        lambda _p: [0.0, 100.0, 200.0, 700.0, 1200.0],
    )

    profile = Profile(name="default")  # no segmentation kwarg
    source = SourceMeta(path=src, container="mp4", duration_sec=1200.0, size_bytes=1)
    encoder = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    plan = Plan(source=source, profile=profile, encoder=encoder, plan_hash="x")

    # target_size_sec defaults to 600; first cut should be at kf=700
    # (first kf >= 600 after start=0).
    segments = segmenter.plan_segments(plan)
    assert len(segments) >= 2
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec == 700.0
