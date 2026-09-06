from tools.media_diagnostics import _add_frame, _finite, _stream


def test_nonfinite_timestamps_are_not_measurements() -> None:
    for value in (None, "N/A", "nan", "inf", "-inf"):
        assert _finite(value) is None


def test_audio_samples_use_native_rate_and_detect_repeated_pts() -> None:
    stream = {**_stream("audio"), "sample_rate": 44100}
    for pts in ("0", "0", "N/A"):
        _add_frame(stream, {"best_effort_timestamp_time": pts, "nb_samples": "441"})
    assert stream["samples"] == 1323
    assert stream["non_increasing_pts_frames"] == 1
    assert stream["missing_pts_frames"] == 1
    assert stream["last_duration_sec"] == 0.01


def test_unknown_duration_is_not_inferred_from_average_fps() -> None:
    stream = _stream("video")
    _add_frame(stream, {"best_effort_timestamp_time": "1.5"})
    assert stream["last_duration_sec"] is None
    _add_frame(stream, {"best_effort_timestamp_time": "1.6", "pkt_duration_time": "0.05"})
    assert stream["last_duration_sec"] == 0.05
