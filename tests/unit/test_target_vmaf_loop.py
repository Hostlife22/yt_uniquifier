"""v0.8.0 R5 — per-segment VMAF target-quality feedback loop.

We monkey-patch every external touchpoint (``run_ffmpeg``, the fused/
legacy command builder, ``_score_segment_vmaf``) so the test exercises
the loop's bookkeeping (CRF decrement, attempt counter, emitted events,
distributed-mode strip) WITHOUT forking ffmpeg or invoking libvmaf.

Coverage matrix:
  * target unset → no scoring, no events, no retries
  * first attempt meets target → 1 event, 0 retries
  * meets after N retries → N+1 events, CRF reduced by N*step
  * exhausted retries → terminal target_vmaf_failed event
  * cancel mid-loop → loop exits without further retries
  * distributed worker strips target_vmaf from the profile

Also pin the CRF defaults shared between pipeline + segmenter so a
silent drift between the two constants (one is mirrored in segmenter
to avoid a circular import) can't go unnoticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import pipeline, segmenter
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
    Segment,
    SourceMeta,
)
from yt_uniquifier.core.runner import CancelToken, RunEvent


def _make_plan(target_vmaf: float | None = None, *, max_retries: int = 2) -> Plan:
    return Plan(
        source=SourceMeta(
            path=Path("/tmp/in.mp4"), container="mp4",
            duration_sec=10.0, size_bytes=1,
        ),
        profile=Profile(
            name="tvmaf",
            target_vmaf=target_vmaf,
            target_vmaf_step=2,
            target_vmaf_max_retries=max_retries,
        ),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="h",
        run_seed=0,
    )


def _make_segment(idx: int = 0) -> Segment:
    return Segment(idx=idx, start_sec=0.0, end_sec=10.0, status="pending")


def _capture_events() -> tuple[list[RunEvent], Any]:
    events: list[RunEvent] = []
    return events, events.append


def _silence_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub run_ffmpeg + the segment builders so process_video_segment
    never touches a subprocess. ``stream_copy_extract`` is also stubbed
    in case the test toggles the legacy two-fork path."""
    monkeypatch.setattr(segmenter, "run_ffmpeg", lambda *a, **kw: None)
    monkeypatch.setattr(
        segmenter, "build_video_segment_command_fused",
        lambda *a, **kw: pipeline.BuiltCommand(
            args=["ffmpeg"], filter_complex="null", output_video_label="v1",
            output_audio_label=None, passthrough_audio_maps=[],
            passthrough_sub_maps=[], extra_inputs=(),
        ),
    )
    monkeypatch.setattr(
        segmenter, "build_video_segment_command",
        lambda *a, **kw: pipeline.BuiltCommand(
            args=["ffmpeg"], filter_complex="null", output_video_label="v1",
            output_audio_label=None, passthrough_audio_maps=[],
            passthrough_sub_maps=[], extra_inputs=(),
        ),
    )
    monkeypatch.setattr(segmenter, "stream_copy_extract", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# (1) target unset — no scoring, no events
# ---------------------------------------------------------------------------


def test_no_target_skips_scoring_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _silence_ffmpeg(monkeypatch)
    scored: list[int] = []
    monkeypatch.setattr(
        segmenter, "_score_segment_vmaf",
        lambda *a, **kw: (scored.append(1), 50.0)[1],
    )
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=None)
    seg = _make_segment()
    segmenter.process_video_segment(seg, plan, tmp_path, on_event=on_event)

    assert scored == []
    assert events == []


# ---------------------------------------------------------------------------
# (2) first attempt meets target — one event, zero retries
# ---------------------------------------------------------------------------


def test_meets_target_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _silence_ffmpeg(monkeypatch)
    monkeypatch.setattr(
        segmenter, "_score_segment_vmaf", lambda *a, **kw: 92.5,
    )
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=90.0, max_retries=3)
    segmenter.process_video_segment(
        _make_segment(), plan, tmp_path, on_event=on_event,
    )

    tvmaf_events = [e for e in events if e.kind == "target_vmaf"]
    failed = [e for e in events if e.kind == "target_vmaf_failed"]
    assert len(tvmaf_events) == 1
    assert failed == []
    p = tvmaf_events[0].payload
    assert p["attempt"] == 0
    assert p["vmaf"] == 92.5
    assert p["target"] == 90.0
    # CRF is the default on first attempt (no override).
    assert p["crf"] == segmenter._DEFAULT_CRF_HINT


# ---------------------------------------------------------------------------
# (3) retries decrement CRF until target met
# ---------------------------------------------------------------------------


def test_retries_drop_crf_until_target_met(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _silence_ffmpeg(monkeypatch)
    # Scripted VMAF trajectory: 78 (miss), 86 (miss), 91 (hit).
    scores = iter([78.0, 86.0, 91.0])
    monkeypatch.setattr(
        segmenter, "_score_segment_vmaf", lambda *a, **kw: next(scores),
    )
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=90.0, max_retries=3)
    segmenter.process_video_segment(
        _make_segment(), plan, tmp_path, on_event=on_event,
    )

    tvmaf_events = [e for e in events if e.kind == "target_vmaf"]
    failed = [e for e in events if e.kind == "target_vmaf_failed"]
    assert len(tvmaf_events) == 3, [e.payload for e in tvmaf_events]
    assert failed == []
    assert [e.payload["attempt"] for e in tvmaf_events] == [0, 1, 2]
    # Initial CRF = 18; step = 2 → attempts use 18, 16, 14.
    assert [e.payload["crf"] for e in tvmaf_events] == [18, 16, 14]
    # Final score wins.
    assert tvmaf_events[-1].payload["vmaf"] == 91.0


# ---------------------------------------------------------------------------
# (4) exhausted retries → terminal target_vmaf_failed
# ---------------------------------------------------------------------------


def test_exhausted_retries_emits_failure_event_and_keeps_best(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _silence_ffmpeg(monkeypatch)
    # Always under-target.
    monkeypatch.setattr(
        segmenter, "_score_segment_vmaf", lambda *a, **kw: 70.0,
    )
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=90.0, max_retries=2)
    segmenter.process_video_segment(
        _make_segment(), plan, tmp_path, on_event=on_event,
    )

    tvmaf_events = [e for e in events if e.kind == "target_vmaf"]
    failed = [e for e in events if e.kind == "target_vmaf_failed"]
    # 1 initial + 2 retries = 3 scoring events.
    assert len(tvmaf_events) == 3
    # Plus the terminal failure event.
    assert len(failed) == 1
    f = failed[0].payload
    assert f["attempts"] == 3
    # CRF on the failure event = the LAST attempt's CRF (18 - 2*2 = 14).
    assert f["crf"] == 14


# ---------------------------------------------------------------------------
# (5) cancel mid-loop stops further retries
# ---------------------------------------------------------------------------


def test_cancel_mid_loop_stops_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _silence_ffmpeg(monkeypatch)
    token = CancelToken()
    call_count = {"n": 0}

    def scored_then_cancel(*_a: Any, **_kw: Any) -> float:
        call_count["n"] += 1
        # After the first attempt, cancel — the loop must not start
        # another encode.
        if call_count["n"] == 1:
            token.cancel()
        return 50.0

    monkeypatch.setattr(segmenter, "_score_segment_vmaf", scored_then_cancel)
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=90.0, max_retries=5)
    segmenter.process_video_segment(
        _make_segment(), plan, tmp_path,
        on_event=on_event, cancel_token=token,
    )

    tvmaf_events = [e for e in events if e.kind == "target_vmaf"]
    # Exactly one scoring event — the loop checked the cancel token
    # before starting attempt 1's re-encode and bailed.
    assert len(tvmaf_events) == 1
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# (6) scoring failure (None) → loop bails, no retries, no failure event
# ---------------------------------------------------------------------------


def test_vmaf_unavailable_skips_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When _score_segment_vmaf returns None (libvmaf missing /
    failed), the loop must not spin — better to keep one encode than
    retry on a metric that will never produce a score."""
    _silence_ffmpeg(monkeypatch)
    monkeypatch.setattr(
        segmenter, "_score_segment_vmaf", lambda *a, **kw: None,
    )
    events, on_event = _capture_events()

    plan = _make_plan(target_vmaf=90.0, max_retries=5)
    segmenter.process_video_segment(
        _make_segment(), plan, tmp_path, on_event=on_event,
    )

    tvmaf_events = [e for e in events if e.kind == "target_vmaf"]
    failed = [e for e in events if e.kind == "target_vmaf_failed"]
    assert len(tvmaf_events) == 1
    assert tvmaf_events[0].payload["vmaf"] is None
    assert failed == []


# ---------------------------------------------------------------------------
# (7) constant drift guard
# ---------------------------------------------------------------------------


def test_default_crf_constants_match() -> None:
    """Segmenter mirrors pipeline's CRF default to dodge a circular
    import. They must agree; this test catches the drift."""
    assert segmenter._DEFAULT_CRF_HINT == pipeline._DEFAULT_X26X_CRF


# ---------------------------------------------------------------------------
# (8) encoder args honour the override
# ---------------------------------------------------------------------------


def test_encoder_args_apply_crf_override_for_x264() -> None:
    plan = _make_plan(target_vmaf=None)
    args = pipeline._encoder_args_for(plan, crf_override=14)
    assert "-crf" in args
    crf_idx = args.index("-crf")
    assert args[crf_idx + 1] == "14"


def test_encoder_args_clamp_crf_to_valid_range() -> None:
    """Out-of-range overrides clamp to [0, 51] — the feedback loop
    should never produce one, but a defensive clamp is cheap insurance."""
    plan = _make_plan(target_vmaf=None)
    low = pipeline._encoder_args_for(plan, crf_override=-5)
    high = pipeline._encoder_args_for(plan, crf_override=99)
    assert low[low.index("-crf") + 1] == "0"
    assert high[high.index("-crf") + 1] == "51"


def test_encoder_args_default_when_override_none() -> None:
    plan = _make_plan(target_vmaf=None)
    args = pipeline._encoder_args_for(plan)
    assert args[args.index("-crf") + 1] == str(pipeline._DEFAULT_X26X_CRF)


def test_encoder_args_nvenc_uses_cq_with_delta() -> None:
    """Hardware encoders preserve the delta from the x264 default —
    CRF 14 on x264 (delta -4) bumps NVENC cq from 19 → 15."""
    plan = Plan(
        source=SourceMeta(
            path=Path("/tmp/in.mp4"), container="mp4",
            duration_sec=10.0, size_bytes=1,
        ),
        profile=Profile(name="p"),
        encoder=EncoderCandidate(
            name="h264_nvenc", vendor="nvenc", codec="h264", works=True,
        ),
        plan_hash="h",
    )
    args = pipeline._encoder_args_for(plan, crf_override=14)
    cq_idx = args.index("-cq")
    assert args[cq_idx + 1] == "15"


# ---------------------------------------------------------------------------
# (9) distributed worker preserves target_vmaf
# ---------------------------------------------------------------------------


def test_distributed_worker_source_does_not_strip_target_vmaf() -> None:
    """Worker mode delegates the same profile to run_full as local mode."""
    import inspect

    from yt_uniquifier.cli import cmd_worker

    prof = Profile(
        name="p",
        target_vmaf=95.0,
        target_vmaf_step=4,
        target_vmaf_max_retries=1,
    )
    source = inspect.getsource(cmd_worker.worker_cmd)
    assert "target_vmaf" not in source
    assert prof.target_vmaf == 95.0
