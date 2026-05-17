"""Sanity for pydantic models: round-trip JSON, frozen behaviour, defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _sample_source(tmp_path: Path) -> SourceMeta:
    return SourceMeta(
        path=tmp_path / "movie.mp4",
        container="mp4",
        duration_sec=7200.0,
        size_bytes=4_500_000_000,
        video=[
            VideoStream(
                index=0,
                codec="h264",
                width=1920,
                height=1080,
                fps=23.976,
                duration_sec=7200.0,
                pix_fmt="yuv420p",
                bit_rate=8_000_000,
                color=HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709", space="bt709"),
                is_default=True,
            )
        ],
        audio=[
            AudioStream(
                index=1,
                codec="aac",
                sample_rate=48000,
                channels=2,
                channel_layout="stereo",
                bit_rate=256_000,
                language="eng",
                is_default=True,
            )
        ],
    )


def _sample_profile() -> Profile:
    return Profile(
        name="medium",
        description="balanced",
        transforms=[
            TransformConfig(id="video.crop_resize", params={"max_strength": 0.02}),
            TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.0008, "tempo": 1.0}),
        ],
        seed=42,
    )


def test_plan_json_round_trip(tmp_path: Path) -> None:
    plan = Plan(
        source=_sample_source(tmp_path),
        profile=_sample_profile(),
        encoder=EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        plan_hash="0" * 16,
    )
    raw = plan.model_dump_json()
    restored = Plan.model_validate_json(raw)
    assert restored == plan


def test_plan_is_frozen(tmp_path: Path) -> None:
    plan = Plan(
        source=_sample_source(tmp_path),
        profile=_sample_profile(),
        encoder=EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        plan_hash="abc",
    )
    with pytest.raises((TypeError, ValueError)):
        plan.plan_hash = "changed"  # type: ignore[misc]


def test_profile_rejects_unknown_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
        Profile.model_validate({"name": "x", "unknown_field": True})


def test_transform_config_default_enabled() -> None:
    tc = TransformConfig(id="video.noise")
    assert tc.enabled is True
    assert tc.params == {}


def test_hdr_defaults() -> None:
    info = HDRInfo(is_hdr=False)
    assert info.transfer == "unknown"
    assert info.primaries == "unknown"
    assert info.bit_depth == 8


def test_plan_dump_mode_json_serializes_path(tmp_path: Path) -> None:
    plan = Plan(
        source=_sample_source(tmp_path),
        profile=_sample_profile(),
        encoder=EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        plan_hash="x",
    )
    data = plan.model_dump(mode="json")
    # Path must be JSON-serializable as string.
    json.dumps(data)
