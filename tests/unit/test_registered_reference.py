"""Registered-reference provenance, resource, and cancellation contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import (
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.qa import registration
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.seed_resolver import derive_segment_seed


def _plan(source_path: Path) -> Plan:
    source_path.write_bytes(b"authorized-source-a")
    source = SourceMeta(
        path=source_path,
        container="mp4",
        duration_sec=2.0,
        size_bytes=source_path.stat().st_size,
        video=[VideoStream(
            index=0,
            codec="h264",
            width=320,
            height=180,
            fps=24.0,
            duration_sec=2.0,
            pix_fmt="yuv420p",
            color=HDRInfo(
                is_hdr=False,
                transfer="bt709",
                primaries="bt709",
                space="bt709",
            ),
        )],
    )
    return Plan(
        source=source,
        profile=Profile(name="registered-reference"),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="ab" * 8,
        run_seed=42,
    )


def test_reference_provenance_is_path_independent_and_content_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    plan = _plan(source)
    monkeypatch.setattr(registration, "_ffmpeg_version_digest", lambda: "ffmpeg")
    first = registration.reference_provenance_key(plan, target_segment_sec=600.0)

    moved = tmp_path / "moved.mp4"
    source.replace(moved)
    moved_plan = plan.model_copy(update={
        "source": plan.source.model_copy(update={"path": moved}),
    })
    assert registration.reference_provenance_key(
        moved_plan, target_segment_sec=600.0
    ) == first

    moved.write_bytes(b"authorized-source-b")
    assert moved.stat().st_size == plan.source.size_bytes
    assert registration.reference_provenance_key(
        moved_plan, target_segment_sec=600.0
    ) != first


def test_reference_provenance_binds_seed_and_segmentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path / "source.mp4")
    monkeypatch.setattr(registration, "_ffmpeg_version_digest", lambda: "ffmpeg")
    baseline = registration.reference_provenance_key(plan, target_segment_sec=600.0)
    different_seed = plan.model_copy(update={"run_seed": 43})

    assert registration.reference_provenance_key(
        different_seed, target_segment_sec=600.0
    ) != baseline
    assert registration.reference_provenance_key(
        plan, target_segment_sec=60.0
    ) != baseline


def test_reference_generation_honours_pre_cancel_without_ffmpeg(
    tmp_path: Path,
) -> None:
    token = CancelToken()
    token.cancel()

    with pytest.raises(PipelineError, match="cancelled"):
        registration.build_transformed_reference(
            _plan(tmp_path / "source.mp4"),
            tmp_path / "reference.mkv",
            cancel_token=token,
        )


def test_reference_budget_fails_before_large_temp_encode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path / "source.mp4")
    monkeypatch.setenv("YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES", "1")

    with pytest.raises(PipelineError, match="bounded disk budget"):
        registration.build_transformed_reference(plan, tmp_path / "reference.mkv")


def test_reference_reuses_divergent_segment_seeds_and_ffv1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _plan(tmp_path / "source.mp4")
    plan = base.model_copy(update={
        "profile": base.profile.model_copy(update={"seed_strategy": "divergent"}),
    })
    segments = [
        Segment(idx=0, start_sec=0.0, end_sec=1.0),
        Segment(idx=1, start_sec=1.0, end_sec=2.0),
    ]
    calls: list[tuple[int, list[str]]] = []
    monkeypatch.setattr(registration, "_check_reference_budget", lambda *_args: None)
    monkeypatch.setattr(registration, "plan_segments", lambda *_args: segments)
    monkeypatch.setattr(
        registration, "reference_provenance_key", lambda *_args, **_kwargs: "provenance"
    )

    def build(segment_plan, segment, _source, output, **kwargs):
        calls.append((segment_plan.run_seed, kwargs["_video_encoder_args_override"]))
        return BuiltCommand(args=["ffmpeg"], filter_complex="graph")

    def run(command, *, output, **_kwargs):
        assert command.filter_complex == "graph"
        output.write_bytes(b"lossless-segment")

    def concat(_segments, _audio, output, _metadata, **_kwargs):
        output.write_bytes(b"lossless-reference")

    monkeypatch.setattr(registration, "build_video_segment_command_fused", build)
    monkeypatch.setattr(registration, "run_ffmpeg", run)
    monkeypatch.setattr(registration, "concat_segments", concat)

    result = registration.build_transformed_reference(
        plan, tmp_path / "registered.mkv", target_segment_sec=1.0,
    )

    assert [seed for seed, _args in calls] == [
        derive_segment_seed(plan.plan_hash, index, plan.run_seed) for index in range(2)
    ]
    assert all(args[:2] == ["-c:v", "ffv1"] for _seed, args in calls)
    assert result.segments == 2
