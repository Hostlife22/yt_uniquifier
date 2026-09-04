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
    audio_channels: int = 2,
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
                           channels=audio_channels)],
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


def test_job_encoder_capability_failure_is_preflight_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core import encoder as encoder_mod

    src = _source(tmp_path, width=3840, height=2160)
    plan = _plan(src, [])
    monkeypatch.setattr(
        encoder_mod,
        "probe_encoder_for_plan",
        lambda _plan: encoder_mod.EncoderCapabilityResult(
            supported=False,
            width=3840,
            height=2160,
            pix_fmt="yuv420p",
            error="device rejected resolution",
        ),
    )

    findings = preflight(
        src, plan, plan.encoder, verify_encoder_capability=True,
    )

    assert "encoder.capability.unsupported" in _codes(findings)
    assert has_fail(findings)


@pytest.mark.parametrize("channels", [1, 6])
def test_haas_rejects_non_stereo_main_audio(
    tmp_path: Path, channels: int,
) -> None:
    src = _source(tmp_path, audio_channels=channels)
    plan = _plan(src, [TransformConfig(id="audio.haas_stereo")])

    findings = preflight(src, plan, plan.encoder)

    assert "audio.haas_requires_stereo" in _codes(findings)
    assert has_fail(findings)


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


def test_image_subs_fail_for_mp4(tmp_path: Path) -> None:
    src = _source(tmp_path, image_subs=True)
    plan = _plan(src, [TransformConfig(id="audio.loudnorm")])
    f = preflight(src, plan, plan.encoder)
    assert "subs.image_based" in _codes(f)
    assert has_fail(f)


def test_image_subs_are_preserved_in_mkv(tmp_path: Path) -> None:
    src = _source(tmp_path, image_subs=True)
    plan = _plan(
        src, [TransformConfig(id="audio.loudnorm")], output_container="mkv",
    )
    f = preflight(src, plan, plan.encoder)
    assert "subs.image_based" in _codes(f)
    assert not has_fail(f)


def test_loudnorm_missing_warns(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(src, [])  # no loudnorm in profile
    f = preflight(src, plan, plan.encoder)
    assert "loudnorm.missing" in _codes(f)


def test_mismatched_audio_video_playback_rates_fail(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.speed", params={"rate": 0.99}),
        TransformConfig(id="audio.pitch_tempo", params={"tempo": 1.0}),
    ])
    findings = preflight(src, plan, plan.encoder)
    assert "timeline.rate_mismatch" in _codes(findings)
    assert has_fail(findings)


def test_matching_audio_video_playback_rates_pass(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.speed", params={"rate": 0.99}),
        TransformConfig(id="audio.pitch_tempo", params={"tempo": 0.99}),
    ])
    findings = preflight(src, plan, plan.encoder)
    assert "timeline.rate_mismatch" not in _codes(findings)


@pytest.mark.parametrize(
    "transform_id",
    [
        "video.crop_resize",
        "video.fit_aspect",
        "video.rotate",
        "video.mirror",
        "video.blend_b",
        "video.temporal_jitter",
        "video.speed",
        "video.subtitles",
        "video.tonemap_sdr",
    ],
)
def test_target_vmaf_rejects_unregistered_reference_transforms(
    tmp_path: Path, transform_id: str,
) -> None:
    src = _source(tmp_path)
    params: dict[str, object] = {}
    if transform_id == "video.fit_aspect":
        params["target_aspect"] = "16:9"
    elif transform_id == "video.blend_b":
        blend = tmp_path / "blend.mp4"
        blend.touch()
        params["b_video_path"] = str(blend)
    elif transform_id == "video.subtitles":
        subtitles = tmp_path / "captions.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        params["subtitle_path"] = str(subtitles)
    plan = _plan(
        src,
        [TransformConfig(id=transform_id, params=params)],
        target_vmaf=90.0,
    )

    findings = preflight(src, plan, plan.encoder)

    assert "quality.target_vmaf.unregistered_reference" in _codes(findings)
    assert has_fail(findings)


def test_target_vmaf_allows_registered_photometric_transforms(tmp_path: Path) -> None:
    src = _source(tmp_path)
    plan = _plan(
        src,
        [
            TransformConfig(id="video.color_eq"),
            TransformConfig(id="video.noise"),
        ],
        target_vmaf=80.0,
    )

    findings = preflight(src, plan, plan.encoder)

    assert "quality.target_vmaf.unregistered_reference" not in _codes(findings)
    assert not has_fail(findings)


def test_quality_risk_warnings_cover_harmful_combinations(tmp_path: Path) -> None:
    src = _source(tmp_path, width=640, height=360)
    plan = _plan(src, [
        TransformConfig(id="video.fit_aspect", params={"target_aspect": "16:9"}),
        TransformConfig(id="video.crop_resize"),
        TransformConfig(id="video.noise"),
        TransformConfig(id="video.subpixel_sharpen"),
        TransformConfig(id="video.temporal_jitter"),
        TransformConfig(id="audio.spectral_smear"),
        TransformConfig(id="audio.reverb"),
        TransformConfig(id="audio.noise_overlay"),
    ])

    findings = preflight(src, plan, plan.encoder)
    codes = _codes(findings)

    assert "quality.upscale.implicit" not in codes
    assert "quality.upscale.explicit" not in codes
    assert "quality.fit_aspect.resolved_canvas" in codes
    assert "quality.multiple_resample" in codes
    assert "quality.noise_sharpen" in codes
    assert "quality.temporal_jitter" in codes
    assert "quality.audio_effect_stack" in codes
    assert not has_fail(findings)


def test_explicit_upscale_is_reported_but_not_rejected(tmp_path: Path) -> None:
    src = _source(tmp_path, width=1280, height=720)
    plan = _plan(src, [TransformConfig(
        id="video.fit_aspect",
        params={
            "target_aspect": "16:9",
            "target_width": 3840,
            "target_height": 2160,
            "allow_upscale": True,
        },
    )])

    findings = preflight(src, plan, plan.encoder)

    assert "quality.upscale.explicit" in _codes(findings)
    assert not has_fail(findings)


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


# ---- v1.0.1 Task 4: disk-space preflight ----------------------------------


def _disk_usage_stub(*, free_gib: float) -> object:
    """Build a shutil._ntuple_diskusage-style triple with a chosen free size."""
    import collections
    Usage = collections.namedtuple("Usage", ["total", "used", "free"])
    free = int(free_gib * (1024 ** 3))
    return Usage(total=free + (10 * 1024 ** 3), used=10 * 1024 ** 3, free=free)


def test_disk_space_skipped_when_no_work_dir(tmp_path: Path) -> None:
    src = _source(tmp_path, bit_rate=8_000_000)
    plan = _plan(src, [])
    f = preflight(src, plan, plan.encoder)  # no work_dir kwarg
    assert "disk.space.ok" not in _codes(f)
    assert "disk.space.insufficient" not in _codes(f)
    assert "disk.space.tight" not in _codes(f)


def test_disk_space_ok_when_plenty_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _source(tmp_path, bit_rate=4_000_000)
    plan = _plan(src, [])
    # 60 s @ 4 Mbps × 1.3 ≈ 39 MB. 100 GB free is comfortable.
    from yt_uniquifier.core import preflight as preflight_mod
    monkeypatch.setattr(
        preflight_mod._shutil, "disk_usage",
        lambda _p: _disk_usage_stub(free_gib=100),
    )
    f = preflight(src, plan, plan.encoder, work_dir=tmp_path / "work")
    assert "disk.space.ok" in _codes(f)
    assert not has_fail(f)


def test_disk_space_error_when_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Severity fail when free disk < estimated × 1.1.

    Synthesise a multi-hour 4K source (~3 hours @ 50 Mbps) so the
    estimate dwarfs the 1 GiB free we report.
    """
    src = _source(
        tmp_path, bit_rate=50_000_000, width=3840, height=2160,
    )
    # Override duration via model_copy so the estimate sails past 1 GiB.
    long_source = src.model_copy(update={"duration_sec": 3 * 3600})
    plan = _plan(long_source, [])
    from yt_uniquifier.core import preflight as preflight_mod
    monkeypatch.setattr(
        preflight_mod._shutil, "disk_usage",
        lambda _p: _disk_usage_stub(free_gib=1.0),
    )
    f = preflight(long_source, plan, plan.encoder, work_dir=tmp_path / "work")
    assert "disk.space.insufficient" in _codes(f)
    assert has_fail(f)


def test_disk_space_warn_thin_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Severity warn when estimated × 1.1 ≤ free < estimated × 1.5.

    Setup: 1-hour @ 50 Mbps × 1.3 ≈ 27 GiB. 35 GiB free sits in the warn
    band (1.1× = 30 GiB ≤ 35 < 1.5× = 41 GiB).
    """
    src = _source(tmp_path, bit_rate=50_000_000)
    long_source = src.model_copy(update={"duration_sec": 3600})
    plan = _plan(long_source, [])
    from yt_uniquifier.core import preflight as preflight_mod
    monkeypatch.setattr(
        preflight_mod._shutil, "disk_usage",
        lambda _p: _disk_usage_stub(free_gib=35.0),
    )
    f = preflight(long_source, plan, plan.encoder, work_dir=tmp_path / "work")
    assert "disk.space.tight" in _codes(f)
    assert not has_fail(f)


def test_disk_space_resolves_to_existing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If work_dir doesn't exist yet (orchestrator hasn't mkdir'd), the
    check walks up to the nearest existing ancestor for disk_usage.
    """
    src = _source(tmp_path, bit_rate=4_000_000)
    plan = _plan(src, [])
    seen: dict[str, Path] = {}
    from yt_uniquifier.core import preflight as preflight_mod

    def _fake(p: object) -> object:
        seen["probed"] = Path(p)
        return _disk_usage_stub(free_gib=100)

    monkeypatch.setattr(preflight_mod._shutil, "disk_usage", _fake)
    nested = tmp_path / "does" / "not" / "exist"
    preflight(src, plan, plan.encoder, work_dir=nested)
    assert seen["probed"].exists(), (
        "preflight should probe the nearest existing ancestor, not a "
        "path that's still to be mkdir'd"
    )
