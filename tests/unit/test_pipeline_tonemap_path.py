"""Pipeline behaviour when video.tonemap_sdr is in the profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core import pipeline as pipeline_mod
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


@pytest.fixture(autouse=True)
def _stub_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")


def _hdr_src(tmp_path: Path) -> SourceMeta:
    p = tmp_path / "src.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=5.0, size_bytes=1000,
        video=[VideoStream(
            index=0, codec="hevc", width=1920, height=1080, fps=24,
            duration_sec=5.0, pix_fmt="yuv420p10le",
            color=HDRInfo(is_hdr=True, transfer="smpte2084", primaries="bt2020",
                          space="bt2020nc", bit_depth=10),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def _plan(source: SourceMeta, transforms: list[TransformConfig], **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=transforms, **profile_kw)  # type: ignore[arg-type]
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=source, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(source, profile, enc))


def test_tonemap_first_skips_hdr_wrap(tmp_path: Path) -> None:
    src = _hdr_src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr",
                        params={"algorithm": "hable", "peak": 1000.0}),
        TransformConfig(id="video.color_eq", params={"brightness": 0.01}),
        TransformConfig(id="video.noise", params={"strength": 3}),
    ], keep_hdr=False)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    fc = built.filter_complex

    # Tonemap filter present.
    assert "tonemap=hable" in fc
    # No HDR-keep wrap (would emit zscale=transfer=smpte2084 round trip).
    assert "transfer=smpte2084" not in fc
    # color_eq still applied after tonemap.
    assert "eq=brightness=0.01" in fc


def test_tonemap_forces_yuv420p_pix_fmt(tmp_path: Path) -> None:
    src = _hdr_src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="video.color_eq"),
    ], keep_hdr=True)  # keep_hdr ignored when tonemap present
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    fc = built.filter_complex
    # Output pix_fmt is yuv420p (8-bit SDR), not yuv420p10le.
    assert "format=yuv420p10le" not in fc
    assert "format=yuv420p" in fc


def test_tonemap_works_with_libx264_on_hdr_source(tmp_path: Path) -> None:
    """libx264 can't output HDR, but it CAN encode the post-tonemap SDR."""
    src = _hdr_src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="video.crop_resize"),
    ], keep_hdr=False)
    # Should build without raising.
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "tonemap=" in built.filter_complex
    assert "libx264" in built.args


def test_sdr_source_tonemap_still_works(tmp_path: Path) -> None:
    """A user might apply tonemap to an SDR source — pipeline shouldn't crash."""
    p = tmp_path / "src.mp4"
    p.touch()
    src = SourceMeta(
        path=p, container="mp4", duration_sec=5.0, size_bytes=1000,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720,
                           fps=24, duration_sec=5.0, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    plan = _plan(src, [TransformConfig(id="video.tonemap_sdr")])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    # The filter is emitted; ffmpeg may produce a slightly washed-out output
    # but won't fail — useful for batch where source mix is unknown.
    assert "tonemap=" in built.filter_complex
