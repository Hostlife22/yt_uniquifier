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
    monkeypatch.setattr(preflight_mod, "_ffmpeg_has_filter",
                        lambda n: n != "rubberband")
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
    monkeypatch.setattr(preflight_mod, "_ffmpeg_has_filter", lambda _n: True)
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
    monkeypatch.setattr(preflight_mod, "_ffmpeg_has_filter",
                        lambda n: n != "rubberband")
    findings = preflight(src, plan, plan.encoder)
    codes = {f.code for f in findings}
    assert "audio.pitch.rubberband.missing" not in codes
