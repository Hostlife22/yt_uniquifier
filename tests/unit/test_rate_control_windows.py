"""Experimental VBV arms retain every unrelated encoder option."""
import pytest

from tools.rate_control_experiment import scaled_vbv


def test_scaled_vbv_changes_only_two_values():
    original = ["-c:v", "libx264", "-crf", "18", "-maxrate", "1000k",
                "-bufsize", "2M", "-g", "60", "out.mp4"]
    expected = list(original)
    expected[5], expected[7] = "4000k", "8M"
    assert scaled_vbv(original, 4) == expected
    assert original[5] == "1000k"


@pytest.mark.parametrize("multiplier", [0, 1, 33, float("nan"), float("inf")])
def test_scaled_vbv_rejects_invalid_multiplier(multiplier):
    with pytest.raises(ValueError):
        scaled_vbv(["-maxrate", "1000", "-bufsize", "2000"], multiplier)


@pytest.mark.parametrize("args", [["-maxrate"], ["-maxrate", "1000"]])
def test_scaled_vbv_requires_complete_bounded_arm(args):
    with pytest.raises(ValueError):
        scaled_vbv(args, 2)
