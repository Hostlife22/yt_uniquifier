"""HDR-specific preflight: zscale availability + 10-bit encoder."""

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


def _plan(src: SourceMeta, encoder_name: str, **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=[TransformConfig(id="audio.loudnorm")],
                       **profile_kw)  # type: ignore[arg-type]
    vendor = "x265" if encoder_name == "libx265" else (
        "x264" if encoder_name == "libx264" else "videotoolbox"
    )
    codec = "hevc" if "265" in encoder_name or "hevc" in encoder_name else "h264"
    enc = EncoderCandidate(name=encoder_name, vendor=vendor, codec=codec, works=True)
    return Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


@pytest.fixture(autouse=True)
def _reset_filter_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test sets its own zscale availability."""
    preflight_mod._FFMPEG_FILTERS_CACHE.clear()


def test_hdr_keep_libx264_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: True)
    src = _hdr_source(tmp_path)
    plan = _plan(src, "libx264", keep_hdr=True)
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    assert "hdr.encoder.8bit" in _codes(f)


def test_hdr_keep_libx265_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: True)
    src = _hdr_source(tmp_path)
    plan = _plan(src, "libx265", keep_hdr=True)
    f = preflight(src, plan, plan.encoder)
    assert "hdr.encoder.8bit" not in _codes(f)
    assert not has_fail(f)


def test_hdr_dynamic_metadata_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works", lambda _s, _k: True)
    src = _hdr_source(tmp_path)
    src = src.model_copy(update={
        "video": [src.video[0].model_copy(update={
            "color": src.video[0].color.model_copy(update={
                "dynamic_metadata": ("Dolby Vision RPU Data",),
            }),
        })],
    })
    plan = _plan(src, "libx265", keep_hdr=True)

    findings = preflight(src, plan, plan.encoder)

    assert "hdr.dynamic_metadata.unsupported" in _codes(findings)
    assert has_fail(findings)


def test_hdr_static_metadata_requires_verified_libx265(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works", lambda _s, _k: True)
    src = _hdr_source(tmp_path)
    src = src.model_copy(update={
        "video": [src.video[0].model_copy(update={
            "color": src.video[0].color.model_copy(update={
                "mastering_display": (
                    "G(8500,39850)B(6550,2300)R(35400,14600)"
                    "WP(15635,16450)L(10000000,1)"
                ),
                "max_cll": 1000,
                "max_fall": 400,
            }),
        })],
    })
    plan = _plan(src, "hevc_videotoolbox", keep_hdr=True, target_codec="hevc")

    findings = preflight(src, plan, plan.encoder)

    assert "hdr.static_metadata.encoder_unverified" in _codes(findings)
    assert has_fail(findings)


def test_hdr_keep_missing_zscale_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: False)
    src = _hdr_source(tmp_path)
    plan = _plan(src, "libx265", keep_hdr=True)
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    assert "hdr.zscale.missing" in _codes(f)


def test_hdr_without_keep_no_zscale_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An undefined HDR output policy fails without an irrelevant zscale finding."""
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: False)
    src = _hdr_source(tmp_path)
    plan = _plan(src, "libx265", keep_hdr=False)
    f = preflight(src, plan, plan.encoder)
    assert "hdr.zscale.missing" not in _codes(f)
    assert "hdr.output_policy.missing" in _codes(f)
    assert has_fail(f)


def test_hdr_keep_with_blend_b_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: True)
    src = _hdr_source(tmp_path)
    profile = Profile(name="t", keep_hdr=True, target_codec="hevc",
                       transforms=[
                           TransformConfig(id="video.blend_b",
                                            params={"b_video_path": "/tmp/b.mp4"}),
                           TransformConfig(id="audio.loudnorm"),
                       ])
    enc = EncoderCandidate(name="libx265", vendor="x265", codec="hevc", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    f = preflight(src, plan, plan.encoder)
    assert "hdr.blend.unwrapped" in _codes(f)
    # Warning, not fail.
    assert not any(x.code == "hdr.blend.unwrapped" and x.severity == "fail" for x in f)


def test_sdr_source_skips_all_hdr_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works",
                        lambda _spec, _kind: False)
    src = _hdr_source(tmp_path)
    # mutate to SDR
    src = src.model_copy(update={
        "video": [src.video[0].model_copy(update={
            "color": HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709",
                              space="bt709"),
        })],
    })
    plan = _plan(src, "libx265", keep_hdr=True)
    f = preflight(src, plan, plan.encoder)
    hdr_codes = {c for c in _codes(f) if c.startswith("hdr.")}
    assert not hdr_codes
