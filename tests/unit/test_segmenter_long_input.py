"""plan_segments on a mocked 2-hour keyframe list."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core import segmenter as seg_mod
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)


def _plan_2h(tmp_path: Path) -> Plan:
    src = tmp_path / "long.mp4"
    src.touch()
    source = SourceMeta(
        path=src, container="mp4", duration_sec=7200.0, size_bytes=10**9,
        video=[VideoStream(index=0, codec="h264", width=1920, height=1080,
                           fps=24.0, duration_sec=7200.0, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    return Plan(
        source=source,
        profile=Profile(name="t"),
        encoder=EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        plan_hash="long" * 4,
        run_seed=0,
    )


def test_long_input_produces_reasonable_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pretend the encoder gave a keyframe every 5 sec across the 7200-sec file.
    keyframes = [i * 5.0 for i in range(7200 // 5 + 1)]
    monkeypatch.setattr(seg_mod, "list_keyframes", lambda _p: keyframes)

    plan = _plan_2h(tmp_path)
    segments = seg_mod.plan_segments(plan, target_size_sec=600.0)
    # ~12 ten-minute segments expected.
    assert 10 <= len(segments) <= 14
    # No segment longer than ~610 sec (one keyframe over target).
    for s in segments:
        assert s.end_sec - s.start_sec <= 615
    # Coverage [0, duration].
    assert segments[0].start_sec == 0.0
    assert segments[-1].end_sec == pytest.approx(7200.0, abs=5.0)


def test_short_input_one_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seg_mod, "list_keyframes", lambda _p: [0.0, 5.0, 10.0])
    plan = _plan_2h(tmp_path)
    src = plan.source.model_copy(update={"duration_sec": 30.0})
    plan = plan.model_copy(update={"source": src})
    segments = seg_mod.plan_segments(plan, target_size_sec=600.0)
    assert len(segments) == 1
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec == pytest.approx(30.0, abs=0.5)
