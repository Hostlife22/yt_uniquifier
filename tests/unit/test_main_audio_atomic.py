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
