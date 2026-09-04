"""Fault-injection coverage for atomic main-audio publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core import segmenter
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import TransformConfig
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement


def test_audio_encode_failure_preserves_cached_audio_and_removes_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(
        _src(tmp_path),
        [TransformConfig(id="audio.eq", params={"low_gain_db": 1.0})],
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    cached = work_dir / "main_audio.m4a"
    cached.write_bytes(b"previous-good-audio")

    monkeypatch.setattr(
        segmenter,
        "build_main_audio_command",
        lambda *args, **kwargs: (BuiltCommand(args=["ffmpeg"]), None),
    )
    monkeypatch.setattr(segmenter, "verify_audio_filters_available", lambda plan: None)

    def fail_encode(command: Any, *, output: Path, **kwargs: Any) -> None:
        del command, kwargs
        output.write_bytes(b"partial-audio")
        raise PipelineError("injected audio failure")

    monkeypatch.setattr(segmenter, "run_ffmpeg", fail_encode)

    with pytest.raises(PipelineError, match="injected audio failure"):
        segmenter.process_main_audio(plan, work_dir)

    assert cached.read_bytes() == b"previous-good-audio"
    assert not list(work_dir.glob(".main_audio.*.part.m4a"))


def test_dynamic_loudnorm_fallback_is_logged_and_emitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_src(tmp_path), [TransformConfig(id="audio.loudnorm")])
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    measurement = LoudnormMeasurement(
        input_i=float("-inf"),
        input_tp=-17.0,
        input_lra=0.0,
        input_thresh=-70.0,
        target_offset=float("inf"),
    )
    monkeypatch.setattr(
        segmenter,
        "build_main_audio_command",
        lambda *args, **kwargs: (BuiltCommand(args=["ffmpeg"]), measurement),
    )
    monkeypatch.setattr(segmenter, "verify_audio_filters_available", lambda plan: None)

    def successful_encode(command: Any, *, output: Path, **kwargs: Any) -> None:
        del command
        output.write_bytes(b"encoded-audio")
        log_path = kwargs["log_path"]
        assert isinstance(log_path, Path)
        log_path.write_text(
            '{"normalization_type":"dynamic"}\n',
            encoding="utf-8",
        )

    monkeypatch.setattr(segmenter, "run_ffmpeg", successful_encode)
    events = []

    output, _ = segmenter.process_main_audio(
        plan,
        work_dir,
        on_event=events.append,
    )

    assert output == work_dir / "main_audio.m4a"
    mode_event = next(event for event in events if event.payload.get("stage") == "loudnorm")
    assert mode_event.payload == {
        "stage": "loudnorm",
        "requested_mode": "dynamic",
        "reported_mode": "dynamic",
        "dynamic_fallback": True,
        "fallback_reason": "unusable_pass1_measurement",
    }
    audio_log = (work_dir / "main_audio.m4a").with_suffix(".m4a.log")
    assert '"dynamic_fallback": true' in audio_log.read_text(encoding="utf-8")


def test_ffmpeg_linear_to_dynamic_fallback_gets_distinct_reason(
    tmp_path: Path,
) -> None:
    plan = _plan(_src(tmp_path), [TransformConfig(id="audio.loudnorm")])
    measurement = LoudnormMeasurement(
        input_i=-21.0,
        input_tp=-3.0,
        input_lra=4.0,
        input_thresh=-31.0,
        target_offset=0.0,
    )
    log_path = tmp_path / "main_audio.m4a.log"
    log_path.write_bytes(
        b"old ffmpeg progress\n" * 5000
        + b'{"normalization_type":"dynamic"}\n'
    )
    events = []

    segmenter._record_loudnorm_mode(plan, measurement, log_path, events.append)

    assert events[0].payload["requested_mode"] == "linear"
    assert events[0].payload["reported_mode"] == "dynamic"
    assert events[0].payload["dynamic_fallback"] is True
    assert (
        events[0].payload["fallback_reason"]
        == "ffmpeg_rejected_linear_constraints"
    )
