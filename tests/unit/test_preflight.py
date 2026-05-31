"""Unit tests for preflight matrix."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    SubtitleStream,
    TransformConfig,
    VideoStream,
)
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import has_fail, preflight


def _source(
    tmp_path: Path,
    *,
    fps: float = 24.0,
    hdr: bool = False,
    audio_sr: int = 48000,
    audio_codec: str = "aac",
    container: str = "mp4",
    width: int = 1920,
    height: int = 1080,
    bit_rate: int | None = 5_000_000,
    image_subs: bool = False,
) -> SourceMeta:
    src = tmp_path / "x.mp4"
    src.touch()
    color = (
        HDRInfo(is_hdr=True, transfer="smpte2084", primaries="bt2020",
                space="bt2020nc", bit_depth=10)
        if hdr
        else HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709",
                     space="bt709")
    )
    subs = [
        SubtitleStream(index=3, codec="hdmv_pgs_subtitle", is_image_based=True)
    ] if image_subs else []
    return SourceMeta(
        path=src, container=container, duration_sec=60, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=width, height=height,
                           fps=fps, duration_sec=60,
                           pix_fmt="yuv420p10le" if hdr else "yuv420p",
                           bit_rate=bit_rate, color=color)],
        audio=[AudioStream(index=1, codec=audio_codec, sample_rate=audio_sr,
                           channels=2)],
        subtitle=subs,
    )


def _plan(source: SourceMeta, transforms: list[TransformConfig], **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=transforms, **profile_kw)  # type: ignore[arg-type]
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=source, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(source, profile, enc))


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


def test_clean_source_passes(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert not has_fail(f)


def test_ffmpeg_filter_works_positive() -> None:
    """volume=1.0 is a no-op audio filter present in every ffmpeg build.

    Acts as a sanity check for the dry-run prober itself: if this
    returns False, ffmpeg on PATH is broken and the rest of the
    preflight filter checks would all false-fail.
    """
    import pytest

    from yt_uniquifier.core.preflight import _ffmpeg_filter_works
    try:
        from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin
        ffmpeg_bin()
    except Exception:
        pytest.skip("ffmpeg not on PATH")
    assert _ffmpeg_filter_works("volume=1.0", "audio") is True


def test_ffmpeg_filter_works_negative() -> None:
    """A nonexistent filter must return False.

    Regression for 2026-05-31 real-video matrix Bug #2 — text-parse
    of `ffmpeg -filters` had an intermittent false-positive on
    rubberband; this dry-run path replaces it. A filter name that
    cannot exist in any ffmpeg build verifies the negative case.
    """
    import pytest

    from yt_uniquifier.core.preflight import _ffmpeg_filter_works
    try:
        from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin
        ffmpeg_bin()
    except Exception:
        pytest.skip("ffmpeg not on PATH")
    assert _ffmpeg_filter_works(
        "__no_such_filter_exists__=foo=1", "audio"
    ) is False


def test_tonemap_sdr_input_fails(tmp_path: Path) -> None:
    """SDR source + video.tonemap_sdr in profile must FAIL preflight.

    Regression: real-video matrix run 2026-05-31 hit "Could not open encoder
    before EOF" on 7 SDR inputs against the cid_aware_hdr_to_sdr profile
    because preflight only checked tonemap-order, not tonemap-vs-source.
    """
    src = _source(tmp_path)  # SDR by default
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="audio.loudnorm"),
    ])
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    assert "tonemap.sdr_input" in _codes(f)


def test_tonemap_sdr_with_hdr_input_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HDR source + video.tonemap_sdr is the supported path; must not fail."""
    import pytest  # noqa: F401  (annotation reference)

    from yt_uniquifier.core import preflight as preflight_mod
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda _spec, _kind: True,
    )
    src = _source(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="audio.loudnorm"),
    ])
    f = preflight(src, plan, plan.encoder)
    assert "tonemap.sdr_input" not in _codes(f)
    assert "hdr.tonemap.ok" in _codes(f)


def test_tonemap_sdr_zscale_missing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HDR source + tonemap_sdr profile must FAIL if zscale absent.

    Regression: 2026-05-31 matrix re-run found that
    cid_aware_hdr_to_sdr × HDR-input crashed mid-encode with
    "No such filter: zscale" on a ffmpeg build without zimg —
    preflight was only emitting the OK status, never probing zscale.
    """
    import pytest  # noqa: F401

    from yt_uniquifier.core import preflight as preflight_mod
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda _spec, _kind: False,
    )
    src = _source(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.tonemap_sdr"),
        TransformConfig(id="audio.loudnorm"),
    ])
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    assert "tonemap.zscale.missing" in _codes(f)


def test_hdr_with_color_transforms_fails(tmp_path: Path) -> None:
    src = _source(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.color_eq"),
        TransformConfig(id="audio.loudnorm"),
    ])
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    assert "hdr.color.transforms" in _codes(f)


def test_hdr_with_x264_encoder_fails(tmp_path: Path) -> None:
    src = _source(tmp_path, hdr=True)
    plan = _plan(src, [], keep_hdr=True)
    f = preflight(src, plan, plan.encoder)
    assert has_fail(f)
    # libx264 cannot output 10-bit HDR.
    assert "hdr.encoder.8bit" in _codes(f)


def test_unusual_fps_warns(tmp_path: Path) -> None:
    src = _source(tmp_path, fps=22.5)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "fps.unusual" in _codes(f)
    assert not has_fail(f)


def test_image_subs_warns(tmp_path: Path) -> None:
    src = _source(tmp_path, image_subs=True)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "subs.image_based" in _codes(f)
    assert not has_fail(f)


def test_loudnorm_missing_warns(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(src, [])  # no loudnorm in profile
    f = preflight(src, plan, plan.encoder)
    assert "loudnorm.missing" in _codes(f)


def test_audio_sr_44k_warns(tmp_path: Path) -> None:
    """44.1k is in the allowed set, so no warning."""
    src = _source(tmp_path, audio_sr=44100)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "audio.sr.bad" not in _codes(f)


def test_audio_sr_22k_warns(tmp_path: Path) -> None:
    src = _source(tmp_path, audio_sr=22050)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "audio.sr.bad" in _codes(f)


def test_bitrate_over_ceiling_warns(tmp_path: Path) -> None:
    src = _source(tmp_path, bit_rate=20_000_000, height=1080)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "bitrate.over" in _codes(f)
