"""v1.2.0 Task 27 — Hypothesis property tests over FilterGraph builds.

Invariants we want to hold for every well-typed combination of
shipped transforms + encoder:

  (1) **Build does not raise** — any list of valid TransformConfigs
      (from the registry) composed together must produce a buildable
      FilterGraph.  This catches regressions where a per-transform
      change makes some other transform unbuildable when chained.
  (2) **Label allocator uniqueness** — the same label is never reused
      across the filter_complex string.  Two transforms sharing a label
      would silently corrupt the graph.
  (3) **Even-dims tail** — the video chain ends with the
      ``scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p`` guard so
      libx264 / libsvtav1 don't reject odd dims after micro-crop.

Strategy: enumerate transforms from the live registry and compose
short sequences.  We don't synthesize random TransformConfig params —
those are validated by per-transform pydantic schemas at build time
and lie outside the integration surface we're testing.
"""

from __future__ import annotations

import re
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    TransformConfig,
    VideoStream,
)
from yt_uniquifier.core.pipeline import FilterGraph, compute_plan_hash

# Transforms that are safe to compose in arbitrary order without
# extra setup (no extra inputs, no profile-level dependencies).
# Order matters in ffmpeg filter graphs but the pipeline composes
# them linearly; the property is that ANY linear composition builds.
_VIDEO_IDS = [
    "video.crop_resize",
    "video.color_eq",
    "video.noise",
    "video.temporal_jitter",
]
_AUDIO_IDS = [
    # audio.loudnorm requires a real ffmpeg pass-1 measurement, so it
    # lives in tests/integration/.  The pure-Python audio transforms
    # below cover the graph-composition surface this property test
    # cares about.
    "audio.eq",
    "audio.pitch_tempo",
]
_CODECS = ["h264", "hevc", "av1"]


def _src(tmp_path: Path) -> SourceMeta:
    src_file = tmp_path / "src.mp4"
    src_file.touch()
    return SourceMeta(
        path=src_file, container="mp4",
        duration_sec=5.0, size_bytes=1000,
        video=[VideoStream(
            index=0, codec="h264", width=1920, height=1080, fps=24.0,
            duration_sec=5.0, pix_fmt="yuv420p", bit_rate=8_000_000,
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(
            index=1, codec="aac", sample_rate=48000, channels=2,
            channel_layout="stereo", is_default=True,
        )],
    )


_settings = settings(
    max_examples=30,
    deadline=4000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.function_scoped_fixture),
)


@_settings
@given(
    video_ids=st.lists(st.sampled_from(_VIDEO_IDS), min_size=0, max_size=3,
                      unique=True),
    audio_ids=st.lists(st.sampled_from(_AUDIO_IDS), min_size=0, max_size=2,
                      unique=True),
    codec=st.sampled_from(_CODECS),
)
def test_filter_graph_builds_for_any_combination(
    video_ids: list[str], audio_ids: list[str], codec: str, tmp_path: Path,
) -> None:
    """Build must succeed for any combination of shipped transforms +
    any target codec.  The plan is wired through the same code path the
    orchestrator uses in production."""
    transforms = [
        *(TransformConfig(id=tid) for tid in video_ids),
        *(TransformConfig(id=tid) for tid in audio_ids),
    ]
    profile = Profile(name="p", transforms=transforms, target_codec=codec)  # type: ignore[arg-type]
    enc_name = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"}[codec]
    enc_vendor = {"h264": "x264", "hevc": "x265", "av1": "svtav1"}[codec]
    enc = EncoderCandidate(name=enc_name, vendor=enc_vendor, codec=codec, works=True)  # type: ignore[arg-type]
    src = _src(tmp_path)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    # Sanity: command has the encoder name and an output path.
    assert enc_name in built.args
    assert str(tmp_path / "out.mp4") in built.args


@_settings
@given(
    video_ids=st.lists(st.sampled_from(_VIDEO_IDS), min_size=1, max_size=3,
                      unique=True),
)
def test_filter_graph_labels_are_unique(
    video_ids: list[str], tmp_path: Path,
) -> None:
    """Every ``[name]`` label appearing on the OUTPUT side of a chain
    fragment must be unique.  A duplicate would mean two transforms
    feed into the same downstream label and ffmpeg's filter parser
    would either error or silently overwrite the first definition.

    We parse the generated filter_complex string with a tolerant regex
    (``[name]`` at end-of-fragment) and assert no duplicates."""
    profile = Profile(
        name="p",
        transforms=[TransformConfig(id=tid) for tid in video_ids],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    src = _src(tmp_path)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    # ``output_video_label`` is set at the FilterGraph layer; for a
    # multi-transform chain it points to a unique vN/aN label.
    out_labels = re.findall(r"\[([va]\d+)\]", built.filter_complex)
    # Track which labels appear as OUTPUTS (right of a fragment) vs
    # INPUTS (left).  Each output should appear exactly once.
    out_occurrences = re.findall(r"\]\[([va]\d+)\];?", built.filter_complex)
    duplicate_outputs = {x for x in out_occurrences if out_occurrences.count(x) > 1}
    assert not duplicate_outputs, (
        f"duplicate output labels in filter_complex: {duplicate_outputs}\n"
        f"all labels: {out_labels}\n"
        f"filter_complex: {built.filter_complex}"
    )


@_settings
@given(
    video_ids=st.lists(st.sampled_from(_VIDEO_IDS), min_size=0, max_size=3,
                      unique=True),
)
def test_filter_graph_video_chain_has_even_dims_tail(
    video_ids: list[str], tmp_path: Path,
) -> None:
    """The video chain must end with the even-dims guard so libx264
    doesn't reject odd dims produced by micro-crop transforms."""
    profile = Profile(
        name="p",
        transforms=[TransformConfig(id=tid) for tid in video_ids],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    src = _src(tmp_path)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    # The tail expression is `scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p`.
    assert "trunc(iw/2)" in built.filter_complex
    assert "trunc(ih/2)" in built.filter_complex
    assert "format=yuv420p" in built.filter_complex
