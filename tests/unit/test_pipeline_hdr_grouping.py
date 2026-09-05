"""Tests that the pipeline groups color transforms into a single zscale wrap
for HDR sources, and leaves SDR sources unchanged.
"""

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
from yt_uniquifier.core.pipeline import FilterGraph, _group_runs, compute_plan_hash


@pytest.fixture(autouse=True)
def _stub_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")


def _src(tmp_path: Path, *, hdr: bool = False) -> SourceMeta:
    color = (
        HDRInfo(is_hdr=True, transfer="smpte2084", primaries="bt2020",
                space="bt2020nc", bit_depth=10)
        if hdr
        else HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709", space="bt709")
    )
    p = tmp_path / "src.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=5.0, size_bytes=1000,
        video=[VideoStream(index=0, codec="hevc" if hdr else "h264",
                           width=1280, height=720, fps=24.0, duration_sec=5.0,
                           pix_fmt="yuv420p10le" if hdr else "yuv420p",
                           color=color)],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def _plan(source: SourceMeta, transforms: list[TransformConfig], **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=transforms, **profile_kw)  # type: ignore[arg-type]
    enc = EncoderCandidate(name="libx265", vendor="x265", codec="hevc", works=True)
    return Plan(source=source, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(source, profile, enc))


def test_group_runs_alternating() -> None:
    tcs = [
        TransformConfig(id="video.crop_resize"),
        TransformConfig(id="video.color_eq"),
        TransformConfig(id="video.noise"),
        TransformConfig(id="video.rotate"),
        TransformConfig(id="video.color_eq"),
    ]
    is_color = {"video.color_eq", "video.noise"}
    runs = _group_runs(tcs, lambda i: i in is_color)
    assert [(flag, len(items)) for flag, items in runs] == [
        (False, 1),  # crop_resize
        (True, 2),   # color_eq, noise
        (False, 1),  # rotate
        (True, 1),   # color_eq
    ]


def test_group_runs_all_same() -> None:
    tcs = [TransformConfig(id="video.color_eq"), TransformConfig(id="video.noise")]
    runs = _group_runs(tcs, lambda i: True)
    assert len(runs) == 1 and runs[0][0] is True


def test_group_runs_empty() -> None:
    assert _group_runs([], lambda i: True) == []


def test_hdr_keep_wraps_color_run(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.crop_resize", params={"rng_seed": 7}),
        TransformConfig(id="video.color_eq", params={"brightness": 0.01}),
        TransformConfig(id="video.noise", params={"strength": 3}),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    fc = built.filter_complex
    # zscale wrap is present.
    assert "zscale=transfer=linear:matrix=gbr:npl=100" in fc
    assert "zscale=transfer=smpte2084:matrix=bt2020nc:npl=100" in fc
    # color_eq and noise are inside the wrap (one comma-joined run).
    # crop_resize is NOT wrapped (geometry).
    assert "crop=iw*" in fc  # crop_resize geometry pre-wrap
    # No second zscale roundtrip for a single run.
    assert fc.count("zscale=transfer=linear") == 1


def test_hdr_keep_two_color_runs_two_wraps(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),
        TransformConfig(id="video.rotate"),    # breaks the color run
        TransformConfig(id="video.noise"),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    fc = built.filter_complex
    # Two color runs → two wraps.
    assert fc.count("zscale=transfer=linear") == 2


def test_hdr_without_keep_does_not_wrap(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),
    ], keep_hdr=False)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "zscale" not in built.filter_complex


def test_sdr_with_keep_hdr_flag_does_not_wrap(tmp_path: Path) -> None:
    """keep_hdr=True is meaningless on SDR sources — no wrap is emitted."""
    src = _src(tmp_path, hdr=False)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "zscale" not in built.filter_complex


def test_hdr_rotate_uses_hdr_safe_fillcolor(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.rotate", params={"degrees": 0.3}),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "fillcolor=0x101010" in built.filter_complex
    assert "fillcolor=black" not in built.filter_complex


def test_sdr_rotate_uses_black(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.rotate", params={"degrees": 0.3}),
    ])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "fillcolor=black" in built.filter_complex
    assert "fillcolor=0x101010" not in built.filter_complex


def test_hdr_output_pix_fmt_10bit(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "yuv420p10le" in built.filter_complex
