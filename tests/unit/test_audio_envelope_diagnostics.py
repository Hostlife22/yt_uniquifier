import numpy as np
import pytest

from tools.media_diagnostics import envelope_alignment
from tools.rate_control_experiment import observed_bands, without_vbv


def test_delayed_audio_cannot_hide_behind_equal_endpoints() -> None:
    rng = np.random.default_rng(42)
    source = np.repeat(rng.random((1000, 2)), 10, axis=0)
    output = np.zeros_like(source)
    output[1320:] = source[:-1320]
    result = envelope_alignment(source, output, sample_rate=1000)
    assert all(c["status"] == "measured" for c in result["channels"])
    assert all(c["lag_sec"] == pytest.approx(1.32) for c in result["channels"])


def test_permuted_surround_and_silence() -> None:
    rng = np.random.default_rng(8)
    source = np.repeat(rng.random((500, 6)), 10, axis=0)
    permutation = [2, 1, 0, 3, 5, 4]
    result = envelope_alignment(source, source[:, permutation], sample_rate=1000)
    matrix = np.array(result["zero_lag_channel_matrix"])
    assert matrix.argmax(axis=1).tolist() == permutation
    silent = envelope_alignment(np.zeros((5000, 6)), np.zeros((5000, 6)), sample_rate=1000)
    assert all(c["status"] == "not_verified" for c in silent["channels"])


def test_invalid_pcm_rejected() -> None:
    with pytest.raises(ValueError, match="nonfinite"):
        envelope_alignment(np.full((100, 1), np.nan), np.zeros((100, 1)), sample_rate=1000)
    with pytest.raises(ValueError, match="topology"):
        envelope_alignment(np.zeros((100, 2)), np.zeros((100, 6)), sample_rate=1000)


def test_periodic_envelope_is_ambiguous_not_verified() -> None:
    signal = np.tile(np.arange(100) / 100, 100).reshape(-1, 1)
    result = envelope_alignment(signal, signal, sample_rate=1000)
    assert result["channels"][0]["status"] == "not_verified"


def test_experiment_changes_only_vbv_options() -> None:
    command = ["ffmpeg", "-crf", "18", "-maxrate", "123", "-bufsize", "246", "out.mp4"]
    assert without_vbv(command) == ["ffmpeg", "-crf", "18", "out.mp4"]
    assert command[3] == "-maxrate"
    with pytest.raises(ValueError):
        without_vbv(["-maxrate"])


def test_unlabelled_repeats_do_not_create_production_thresholds() -> None:
    rows = [
        {"policy": "crf_only", "source_sha256": "same", "vmaf": score}
        for score in (94, 96, None, float("nan"))
    ]
    result = observed_bands(rows)
    assert result["proposed_production_thresholds"] is None
    group = result["groups"]["crf_only"]
    assert group["unique_source_files"] == 1
    assert group["observed_bands"]["vmaf"] == {
        "measured": 2, "missing": 2, "min": 94, "median": 95, "max": 96,
    }
