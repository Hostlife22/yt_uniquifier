import math
import re

import pytest

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.audio_pitch import PitchTempoParams, cascade_atempo
from yt_uniquifier.core.transforms.base import LabelAllocator


@pytest.mark.parametrize("sample_rate,pitch", [(44100, 1.0004), (8000, 1.00006), (48000, 1.012345)])
@pytest.mark.parametrize("target_tempo", [0.5, 1.0, 2.0])
def test_compensation_uses_actual_integer_sample_clock(
    sample_rate: int, pitch: float, target_tempo: float,
) -> None:
    chain = get("audio.pitch_tempo").build(
        PitchTempoParams(sample_rate=sample_rate, pitch=pitch, tempo=target_tempo),
        LabelAllocator(), "0:a:0",
    ).filter_str
    rate_expression = chain.split("asetrate=", 1)[1].split(",", 1)[0]
    actual_rate = round(math.prod(float(part) for part in rate_expression.split("*")))
    tempo = math.prod(float(value) for value in re.findall(r"atempo=([\d.]+)", chain))
    # Keep the clock error below one sample over a three-hour timeline.
    assert abs(actual_rate / sample_rate * tempo - target_tempo) * 10800 < 1 / sample_rate


@pytest.mark.parametrize("target", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_tempo_cannot_hang_cascade(target: float) -> None:
    with pytest.raises(ValueError):
        cascade_atempo(target)
