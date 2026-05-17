"""Preflight changes when video.tonemap_sdr is in the profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core import preflight as preflight_mod
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


def _hdr_source(tmp_path: Path) -> SourceMeta:
    p = tmp_path / "hdr.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=10, size_bytes=100,
        video=[VideoStream(
            index=0, codec="hevc", width=1920, height=1080, fps=24.0,
            duration_sec=10, pix_fmt="yuv420p10le",
            color=HDRInfo(is_hdr=True, transfer="smpte2084", primaries="bt2020",
                          space="bt2020nc", bit_depth=10),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def _plan(src: SourceMeta, transforms: list[TransformConfig],
          encoder_name: str = "libx264", **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=transforms, **profile_kw)  # type: ignore[arg-type]
    vendor = ("x264" if encoder_name == "libx264" else
              ("x265" if encoder_name == "libx265" else "videotoolbox"))
    codec = "hevc" if "265" in encoder_name or "hevc" in encoder_name else "h264"
    enc = EncoderCandidate(name=encoder_name, vendor=vendor,  # type: ignore[arg-type]
                            codec=codec, works=True)
    return Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


@pytest.fixture(autouse=True)
def _ffmpeg_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tonemap path doesn't query zscale availability — but other HDR checks do."""
    monkeypatch.setattr(preflight_mod, "_ffmpeg_has_filter", lambda _n: True)


def test_tonemap_present_no_color_fail(tmp_path: Path) -> None:
    src = _hdr_source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="video.color_eq"),     # would normally trip fail
        TransformConfig(id="video.noise"),
    ])  # keep_hdr=False by default
    findings = preflight(src, plan, plan.encoder)
    assert "hdr.color.transforms" not in _codes(findings)
    assert "hdr.tonemap.ok" in _codes(findings)
    assert not has_fail(findings)


def test_tonemap_present_no_encoder_8bit_fail(tmp_path: Path) -> None:
    """libx264 is fine when tonemap converts HDR → SDR upfront."""
    src = _hdr_source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
    ], encoder_name="libx264", keep_hdr=False)
    findings = preflight(src, plan, plan.encoder)
    assert "hdr.encoder.8bit" not in _codes(findings)


def test_tonemap_not_first_warns(tmp_path: Path) -> None:
    src = _hdr_source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),     # tonemap should be first
        TransformConfig(id="video.tonemap_sdr"),
    ])
    findings = preflight(src, plan, plan.encoder)
    assert "tonemap.not_first" in _codes(findings)
    # It's a warning, not a fail.
    assert not has_fail(findings)


def test_tonemap_first_no_warn(tmp_path: Path) -> None:
    src = _hdr_source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="video.color_eq"),
    ])
    findings = preflight(src, plan, plan.encoder)
    assert "tonemap.not_first" not in _codes(findings)


def test_sdr_source_no_hdr_codes(tmp_path: Path) -> None:
    """Tonemap on an SDR source emits no HDR-related findings."""
    src = _hdr_source(tmp_path)
    src = src.model_copy(update={
        "video": [src.video[0].model_copy(update={
            "color": HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709",
                              space="bt709"),
        })],
    })
    plan = _plan(src, [TransformConfig(id="video.tonemap_sdr")])
    findings = preflight(src, plan, plan.encoder)
    assert not {c for c in _codes(findings) if c.startswith("hdr.")}


def test_disabled_tonemap_does_not_suppress_fails(tmp_path: Path) -> None:
    """If tonemap is disabled, the original HDR restrictions still apply."""
    src = _hdr_source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr", enabled=False),
        TransformConfig(id="video.color_eq"),
    ])
    findings = preflight(src, plan, plan.encoder)
    assert "hdr.color.transforms" in _codes(findings)
    assert has_fail(findings)
