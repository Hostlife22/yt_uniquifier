"""Snapshot / behaviour tests for v0.2 transforms."""

from __future__ import annotations

import random

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_eq import AudioEqParams
from yt_uniquifier.core.transforms.audio_pitch import PitchTempoParams
from yt_uniquifier.core.transforms.audio_resample import AudioResampleParams
from yt_uniquifier.core.transforms.audio_spectral_smear import SpectralSmearParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_geom import MirrorParams

# ---- audio.resample --------------------------------------------------------

def test_resample_emits_two_aresample() -> None:
    spec = get("audio.resample")
    c = call_build(spec, AudioResampleParams(intermediate_sr=47999), LabelAllocator(), "0:a:0")
    assert c.filter_str == "aresample=47999,aresample=48000"


def test_resample_custom_targets() -> None:
    spec = get("audio.resample")
    c = call_build(spec, AudioResampleParams(intermediate_sr=44100, target_sr=48000),
                   LabelAllocator(), "0:a:0")
    assert c.filter_str == "aresample=44100,aresample=48000"


# ---- audio.spectral_smear ---------------------------------------------------

def test_smear_default_low_intensity() -> None:
    spec = get("audio.spectral_smear")
    c = call_build(spec, SpectralSmearParams(), LabelAllocator(), "0:a:0")
    assert c.filter_str.startswith("chorus=")
    assert "0.0200" in c.filter_str  # intensity formatted


def test_smear_high_intensity_present() -> None:
    spec = get("audio.spectral_smear")
    c = call_build(spec, SpectralSmearParams(intensity=0.08), LabelAllocator(), "0:a:0")
    assert "0.0800" in c.filter_str


# ---- video.mirror ----------------------------------------------------------

def test_mirror_is_hflip() -> None:
    spec = get("video.mirror")
    c = call_build(spec, MirrorParams(), LabelAllocator(), "0:v:0")
    assert c.filter_str == "hflip"


# ---- pitch_tempo.randomize_within ------------------------------------------

def test_pitch_no_randomize_is_deterministic() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.012, tempo=1.0, randomize_within=0.0)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(99))
    assert c1.filter_str == c2.filter_str
    assert "asetrate=48576" in c1.filter_str


def test_pitch_randomize_same_seed_same_output() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.012, tempo=1.0, randomize_within=0.005)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str


def test_pitch_randomize_different_seed_different_output() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.012, tempo=1.0, randomize_within=0.005)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str != c2.filter_str


def test_pitch_no_rng_does_not_randomize() -> None:
    spec = get("audio.pitch_tempo")
    p = PitchTempoParams(pitch=1.012, tempo=1.0, randomize_within=0.005)
    c = call_build(spec, p, LabelAllocator(), "0:a:0", rng=None)
    # Without rng, pitch stays at exactly 1.012.
    assert "asetrate=48576" in c.filter_str


# ---- audio_eq.randomize_bands ----------------------------------------------

def test_audio_eq_no_randomize() -> None:
    spec = get("audio.eq")
    p = AudioEqParams()
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(99))
    assert c1.filter_str == c2.filter_str


def test_audio_eq_randomize_changes_output() -> None:
    spec = get("audio.eq")
    p = AudioEqParams(randomize_bands=True)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(1))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(2))
    assert c1.filter_str != c2.filter_str


def test_audio_eq_randomize_same_seed_reproducible() -> None:
    spec = get("audio.eq")
    p = AudioEqParams(randomize_bands=True)
    c1 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    c2 = call_build(spec, p, LabelAllocator(), "0:a:0", rng=random.Random(42))
    assert c1.filter_str == c2.filter_str
