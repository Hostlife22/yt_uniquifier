"""Preflight regression for the video.subtitles burn-in transform.

v0.9.0 R2 / F14 — guards against the failure mode where a profile
ships with ``video.subtitles`` enabled but no usable SRT on disk.
Without preflight catching it, the missing-file error surfaces only
when ffmpeg opens the filter graph for the first segment, minutes
into a long encode.
"""

from __future__ import annotations

from pathlib import Path

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
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import has_fail, preflight


def _source(tmp_path: Path) -> SourceMeta:
    src = tmp_path / "in.mp4"
    src.touch()
    color = HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709", space="bt709")
    return SourceMeta(
        path=src, container="mp4", duration_sec=60, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1920, height=1080,
                           fps=24.0, duration_sec=60, pix_fmt="yuv420p",
                           bit_rate=5_000_000, color=color)],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
        subtitle=[],
    )


def _plan_with_subtitles(source: SourceMeta, subtitle_path: str) -> Plan:
    profile = Profile(
        name="t",
        transforms=[TransformConfig(
            id="video.subtitles",
            enabled=True,
            params={"subtitle_path": subtitle_path},
        )],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=source, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(source, profile, enc))


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


def test_missing_subtitle_path_fails(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan_with_subtitles(src, "/nonexistent/does_not_exist.srt")
    findings = preflight(src, plan, plan.encoder)
    assert has_fail(findings)
    assert "subtitles.path.not_found" in _codes(findings)


def test_present_subtitle_path_passes(tmp_path: Path) -> None:
    src = _source(tmp_path)
    sub = tmp_path / "captions.srt"
    sub.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    plan = _plan_with_subtitles(src, str(sub))
    findings = preflight(src, plan, plan.encoder)
    assert "subtitles.path.not_found" not in _codes(findings)
    assert "subtitles.path.missing" not in _codes(findings)
    assert "subtitles.path.bad_extension" not in _codes(findings)


def test_unsupported_extension_fails(tmp_path: Path) -> None:
    src = _source(tmp_path)
    sub = tmp_path / "captions.txt"
    sub.write_text("nope", encoding="utf-8")
    plan = _plan_with_subtitles(src, str(sub))
    findings = preflight(src, plan, plan.encoder)
    assert has_fail(findings)
    assert "subtitles.path.bad_extension" in _codes(findings)


def test_disabled_transform_is_not_checked(tmp_path: Path) -> None:
    """If the transform is disabled, missing-path must not fail."""
    src = _source(tmp_path)
    profile = Profile(
        name="t",
        transforms=[TransformConfig(
            id="video.subtitles",
            enabled=False,
            params={"subtitle_path": "/does_not_exist.srt"},
        )],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    findings = preflight(src, plan, plan.encoder)
    assert "subtitles.path.not_found" not in _codes(findings)


def test_empty_subtitle_path_fails(tmp_path: Path) -> None:
    """An enabled transform with no subtitle_path in params fails fast."""
    src = _source(tmp_path)
    # Bypass pydantic field validation by injecting an empty params
    # dict directly into TransformConfig (which accepts arbitrary
    # dicts). Mirrors what a hand-edited YAML might look like.
    profile = Profile(
        name="t",
        transforms=[TransformConfig(
            id="video.subtitles",
            enabled=True,
            params={},
        )],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    findings = preflight(src, plan, plan.encoder)
    assert has_fail(findings)
    assert "subtitles.path.missing" in _codes(findings)
