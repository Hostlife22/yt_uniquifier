"""A1 (v0.5.5) regression: process_video_segment's _wrap must NOT mutate
the input event's payload.

Before A1, the segmenter wrapper closure used `ev.payload["segment"] = idx`
to inject the segment index. `RunEvent` is `@dataclass(frozen=True)` but
`payload: dict[str, object]` is only shallowly frozen — the dict
contents are mutable. Mutation leaked back to anyone holding the original
reference (Qt queued connection consumers, retry-replay buffers, log
sinks reading asynchronously) and silently violated the frozen-event
contract.

The fix constructs a NEW `RunEvent` with a new payload dict. This test
verifies the contract end-to-end through `process_video_segment` with
all heavy dependencies stubbed.
"""

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
    Segment,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.runner import RunEvent


def _make_plan(tmp_path: Path) -> Plan:
    src = tmp_path / "in.mp4"
    src.touch()
    source = SourceMeta(
        path=src,
        container="mp4",
        duration_sec=60.0,
        size_bytes=1_000_000,
        video=[
            VideoStream(
                index=0, codec="h264", width=320, height=180, fps=24.0,
                duration_sec=60.0, pix_fmt="yuv420p",
                color=HDRInfo(is_hdr=False),
            ),
        ],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    return Plan(
        source=source,
        profile=Profile(name="t"),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="deadbeef" * 2,
        run_seed=0,
    )


def test_wrap_does_not_mutate_input_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path)
    segment = Segment(idx=7, start_sec=0.0, end_sec=10.0, status="pending")

    # Capture the original event runner.py would emit.
    original_event = RunEvent(kind="progress", payload={"out_time_us": 12345})
    original_payload_id = id(original_event.payload)
    original_payload_snapshot = dict(original_event.payload)

    # Stub stream_copy_extract + build_video_segment_command so we never
    # actually fork ffmpeg.
    monkeypatch.setattr(
        seg_mod, "stream_copy_extract",
        lambda seg, src, dst: dst.write_bytes(b"stub"),
    )
    monkeypatch.setattr(
        seg_mod, "build_video_segment_command",
        lambda plan, src, out: type(
            "C", (), {"args": ["ffmpeg", "-version"]},
        )(),
    )

    # Stub run_ffmpeg: invoke on_event with the original event reference,
    # then return.
    def fake_run_ffmpeg(
        cmd, output, *, on_event=None, cancel_token=None,
        log_path=None, extra_env=None,
    ):
        # Touch the output so subsequent checks don't fail.
        Path(output).write_bytes(b"stub")
        if on_event is not None:
            on_event(original_event)

    monkeypatch.setattr(seg_mod, "run_ffmpeg", fake_run_ffmpeg)

    received: list[RunEvent] = []
    seg_mod.process_video_segment(
        segment, plan, tmp_path,
        on_event=received.append,
    )

    # 1. Original event payload was NOT mutated.
    assert original_event.payload == original_payload_snapshot, (
        "_wrap mutated the input event's payload dict"
    )
    assert id(original_event.payload) == original_payload_id, (
        "input payload dict identity must be preserved"
    )

    # 2. Received event has segment=7 (the new event the wrap constructed).
    assert len(received) == 1
    assert received[0].payload.get("segment") == 7
    assert received[0].kind == "progress"

    # 3. The received event is a DIFFERENT object than the original.
    assert received[0] is not original_event
    assert received[0].payload is not original_event.payload


def test_wrap_preserves_caller_supplied_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the upstream event already carries `segment`, _wrap leaves it alone."""
    plan = _make_plan(tmp_path)
    segment = Segment(idx=7, start_sec=0.0, end_sec=10.0, status="pending")

    pre_set_event = RunEvent(
        kind="log",
        payload={"phase": "ffmpeg", "segment": 99},
    )

    monkeypatch.setattr(
        seg_mod, "stream_copy_extract",
        lambda seg, src, dst: dst.write_bytes(b"stub"),
    )
    monkeypatch.setattr(
        seg_mod, "build_video_segment_command",
        lambda plan, src, out: type(
            "C", (), {"args": ["ffmpeg", "-version"]},
        )(),
    )

    def fake_run_ffmpeg(
        cmd, output, *, on_event=None, cancel_token=None,
        log_path=None, extra_env=None,
    ):
        Path(output).write_bytes(b"stub")
        if on_event is not None:
            on_event(pre_set_event)

    monkeypatch.setattr(seg_mod, "run_ffmpeg", fake_run_ffmpeg)

    received: list[RunEvent] = []
    seg_mod.process_video_segment(
        segment, plan, tmp_path,
        on_event=received.append,
    )

    # When caller already set "segment", wrap is a no-op pass-through.
    assert received[0].payload["segment"] == 99
    assert received[0] is pre_set_event
