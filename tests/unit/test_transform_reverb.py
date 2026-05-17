"""audio.reverb transform — aecho presets + intensity scaling."""

from __future__ import annotations

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_reverb import ReverbParams
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


def test_default_small_room_at_baseline_intensity() -> None:
    spec = get("audio.reverb")
    chain = call_build(spec, ReverbParams(), LabelAllocator(), "0:a:0")
    # intensity 0.15 is the baseline → decays printed as-is.
    assert chain.filter_str == "aecho=0.8:0.88:40|60:0.400|0.300"


def test_intensity_scales_decays_linearly() -> None:
    spec = get("audio.reverb")
    chain = call_build(
        spec, ReverbParams(intensity=0.30, style="small_room"),
        LabelAllocator(), "0:a:0",
    )
    # 0.30 / 0.15 = 2× → decays doubled.
    assert "0.800|0.600" in chain.filter_str


def test_zero_intensity_zeroes_decays() -> None:
    spec = get("audio.reverb")
    chain = call_build(
        spec, ReverbParams(intensity=0.0),
        LabelAllocator(), "0:a:0",
    )
    assert "0.000|0.000" in chain.filter_str


def test_hall_preset_has_four_taps() -> None:
    spec = get("audio.reverb")
    chain = call_build(
        spec, ReverbParams(style="hall", intensity=0.15),
        LabelAllocator(), "0:a:0",
    )
    # 4 delay taps, 4 decays.
    assert "100|200|400|800" in chain.filter_str
    decays_part = chain.filter_str.split(":")[-1]
    assert decays_part.count("|") == 3


def test_plate_preset_short_delays() -> None:
    spec = get("audio.reverb")
    chain = call_build(
        spec, ReverbParams(style="plate"),
        LabelAllocator(), "0:a:0",
    )
    assert chain.filter_str.startswith("aecho=0.9:0.92:20|40|80:")
