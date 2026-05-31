"""audio.pitch_tempo method='rubberband' + preflight check."""

from __future__ import annotations

import random
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
from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_pitch import PitchTempoParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


def test_rubberband_method_emits_rubberband_filter() -> None:
    spec = get("audio.pitch_tempo")
    chain = call_build(
        spec, PitchTempoParams(pitch=1.04, method="rubberband"),
        LabelAllocator(), "0:a:0", rng=random.Random(42),
    )
    assert chain.filter_str.startswith("rubberband=pitch=1.040000")
    assert "tempo=1.000000" in chain.filter_str
    # No legacy asetrate path when rubberband is selected.
    assert "asetrate" not in chain.filter_str


def test_asetrate_method_default_back_compat() -> None:
    spec = get("audio.pitch_tempo")
    chain = call_build(
        spec, PitchTempoParams(pitch=1.012),  # default method=asetrate
        LabelAllocator(), "0:a:0", rng=random.Random(42),
    )
    assert "asetrate=48000*1.012000" in chain.filter_str
    assert "rubberband" not in chain.filter_str


def test_rubberband_with_randomize_within_seeded() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.04, method="rubberband", randomize_within=0.01)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c3 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str == c2.filter_str       # same seed → identical
    assert c1.filter_str != c3.filter_str       # different seed → different pitch


def _hdr_source(tmp_path: Path) -> SourceMeta:
    p = tmp_path / "src.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=10, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1280, height=720, fps=24,
                           duration_sec=10, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def _source(
    tmp_path: Path, *, duration_sec: float = 10.0,
    width: int = 1280, height: int = 720,
) -> SourceMeta:
    p = tmp_path / "src.mp4"
    p.touch()
    return SourceMeta(
        path=p, container="mp4", duration_sec=duration_sec, size_bytes=100,
        video=[VideoStream(
            index=0, codec="h264", width=width, height=height, fps=24,
            duration_sec=duration_sec, pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


def _rb_plan(src: SourceMeta, *, method: str = "rubberband") -> Plan:
    profile = Profile(name="t", transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.04, "method": method}),
        TransformConfig(id="audio.loudnorm"),
    ])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))


def _stub_filter_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the rubberband-missing FAIL path so the perf WARN is what we measure."""
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda _spec, _kind: True,
    )


def test_preflight_fails_when_rubberband_filter_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _hdr_source(tmp_path)
    profile = Profile(name="t", transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.04, "method": "rubberband"}),
        TransformConfig(id="audio.loudnorm"),
    ])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    # ffmpeg without rubberband.
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda spec, kind: "rubberband" not in spec,
    )
    findings = preflight(src, plan, plan.encoder)
    codes = {f.code for f in findings}
    assert "audio.pitch.rubberband.missing" in codes
    assert has_fail(findings)


def test_preflight_passes_when_rubberband_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _hdr_source(tmp_path)
    profile = Profile(name="t", transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.04, "method": "rubberband"}),
        TransformConfig(id="audio.loudnorm"),
    ])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda _spec, _kind: True,
    )
    findings = preflight(src, plan, plan.encoder)
    codes = {f.code for f in findings}
    assert "audio.pitch.rubberband.missing" not in codes


def test_preflight_no_check_when_no_rubberband_in_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asetrate-only profile shouldn't query the rubberband filter at all."""
    src = _hdr_source(tmp_path)
    profile = Profile(name="t", transforms=[
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.012}),
        TransformConfig(id="audio.loudnorm"),
    ])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda spec, kind: "rubberband" not in spec,
    )
    findings = preflight(src, plan, plan.encoder)
    codes = {f.code for f in findings}
    assert "audio.pitch.rubberband.missing" not in codes


# --- Perf WARN: rubberband on long / hi-res sources ------------------------


def test_rubberband_short_sd_passes_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30s 720p with rubberband → no perf WARN."""
    _stub_filter_works(monkeypatch)
    src = _source(tmp_path, duration_sec=30.0, width=1280, height=720)
    plan = _rb_plan(src)
    codes = {f.code for f in preflight(src, plan, plan.encoder)}
    assert "audio.pitch.rubberband.slow" not in codes


def test_rubberband_long_source_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """120s 720p with rubberband → exactly one perf WARN."""
    _stub_filter_works(monkeypatch)
    src = _source(tmp_path, duration_sec=120.0, width=1280, height=720)
    plan = _rb_plan(src)
    findings = preflight(src, plan, plan.encoder)
    matches = [f for f in findings if f.code == "audio.pitch.rubberband.slow"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    assert "duration" in matches[0].message


def test_rubberband_4k_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12s 2160p with rubberband → exactly one perf WARN (hi-res trigger)."""
    _stub_filter_works(monkeypatch)
    src = _source(tmp_path, duration_sec=12.0, width=3840, height=2160)
    plan = _rb_plan(src)
    findings = preflight(src, plan, plan.encoder)
    matches = [f for f in findings if f.code == "audio.pitch.rubberband.slow"]
    assert len(matches) == 1
    assert "height" in matches[0].message


def test_rubberband_long_and_4k_emits_single_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5-min 4K source → still exactly one finding (not duplicated per trigger)."""
    _stub_filter_works(monkeypatch)
    src = _source(tmp_path, duration_sec=300.0, width=3840, height=2160)
    plan = _rb_plan(src)
    findings = preflight(src, plan, plan.encoder)
    matches = [f for f in findings if f.code == "audio.pitch.rubberband.slow"]
    assert len(matches) == 1
    # Both triggers should be reported inside the single message body.
    assert "duration" in matches[0].message
    assert "height" in matches[0].message


def test_rubberband_disabled_never_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """method='asetrate' on a 5-min 4K source → no perf WARN."""
    _stub_filter_works(monkeypatch)
    src = _source(tmp_path, duration_sec=300.0, width=3840, height=2160)
    plan = _rb_plan(src, method="asetrate")
    codes = {f.code for f in preflight(src, plan, plan.encoder)}
    assert "audio.pitch.rubberband.slow" not in codes


# --- Defense-in-depth: audio-chain pre-flight re-verify -------------------


def test_verify_audio_filters_passes_when_rubberband_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: rubberband enabled + available → silent return."""
    from yt_uniquifier.core.audio_windows import verify_audio_filters_available

    _stub_filter_works(monkeypatch)
    plan = _rb_plan(_source(tmp_path))
    verify_audio_filters_available(plan)  # must not raise


def test_verify_audio_filters_raises_when_rubberband_lost_post_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filter became unavailable between preflight and audio chain → PipelineError."""
    from yt_uniquifier.core.audio_windows import verify_audio_filters_available
    from yt_uniquifier.core.errors import PipelineError

    plan = _rb_plan(_source(tmp_path))
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda spec, _kind: "rubberband" not in spec,
    )
    with pytest.raises(PipelineError, match="rubberband"):
        verify_audio_filters_available(plan)


def test_verify_audio_filters_skips_when_no_rubberband(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asetrate-only profile → no probe, no error even if filter is 'missing'."""
    from yt_uniquifier.core.audio_windows import verify_audio_filters_available

    plan = _rb_plan(_source(tmp_path), method="asetrate")
    # Even if probe would report False, asetrate path must not call it.
    called: list[str] = []

    def _spy(spec: str, _kind: str) -> bool:
        called.append(spec)
        return False

    monkeypatch.setattr(preflight_mod, "_ffmpeg_filter_works", _spy)
    verify_audio_filters_available(plan)
    assert called == []  # rubberband disabled → probe never invoked


def test_missing_rubberband_filter_still_fails_not_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order-of-checks guard: missing filter → FAIL, not silenced by perf WARN."""
    src = _source(tmp_path, duration_sec=300.0, width=3840, height=2160)
    plan = _rb_plan(src)
    monkeypatch.setattr(
        preflight_mod, "_ffmpeg_filter_works",
        lambda spec, _kind: "rubberband" not in spec,
    )
    findings = preflight(src, plan, plan.encoder)
    codes = {f.code for f in findings}
    assert "audio.pitch.rubberband.missing" in codes
    assert has_fail(findings)
