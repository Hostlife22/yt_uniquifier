"""v1.1.0 Task 13: structured logging + Task 14: run_id correlation.

Verifies that:
  * the configured renderer emits JSON when ``YT_UNIQ_LOG_FORMAT=json``;
  * each event carries the standard keys (timestamp, level, event,
    logger);
  * bound context (run_id, plan_hash) appears on every event without
    per-call boilerplate.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from yt_uniquifier.core import logging_config


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force re-configuration in each test so env tweaks land."""
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)
    # Drop any handlers from a prior test to avoid duplicate output.
    logging.getLogger().handlers.clear()


def _stderr_payload(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Redirect stderr into a capture buffer for the duration of a test."""
    import sys
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    return buf


def test_json_renderer_when_env_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(logging_config.LOG_FORMAT_ENV, "json")
    monkeypatch.setenv(logging_config.LOG_LEVEL_ENV, "INFO")
    buf = _stderr_payload(monkeypatch)

    log = logging_config.get_logger(
        "test.json", run_id="rid-123", plan_hash="ph-abc",
    )
    log.info("hello", extra_key=42)

    payload = buf.getvalue().strip().splitlines()
    assert payload, "expected at least one log line on stderr"
    record = json.loads(payload[-1])
    assert record["event"] == "hello"
    assert record["run_id"] == "rid-123"
    assert record["plan_hash"] == "ph-abc"
    assert record["extra_key"] == 42
    assert record["level"] == "info"
    # ISO-8601 UTC — ends with Z or +00:00.
    assert "timestamp" in record
    assert record["timestamp"].startswith("20"), record["timestamp"]


def test_console_renderer_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(logging_config.LOG_FORMAT_ENV, raising=False)
    buf = _stderr_payload(monkeypatch)

    log = logging_config.get_logger("test.console", run_id="rid-cn")
    log.warning("warming up")

    out = buf.getvalue()
    # Console renderer prints human text, not JSON. Both the event name
    # and the bound context must be visible somewhere on the line.
    assert "warming up" in out
    assert "rid-cn" in out
    # Definitely not JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip().splitlines()[-1])


def test_log_level_env_filters_lower_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(logging_config.LOG_FORMAT_ENV, "json")
    monkeypatch.setenv(logging_config.LOG_LEVEL_ENV, "WARNING")
    buf = _stderr_payload(monkeypatch)

    log = logging_config.get_logger("test.lvl")
    log.info("should be filtered out")
    log.warning("should be kept")

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    events = [json.loads(ln)["event"] for ln in lines]
    assert "should be kept" in events
    assert "should be filtered out" not in events


def test_configure_logging_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple imports / repeated entry-point startup must not stack
    handlers (which would duplicate every log line)."""
    monkeypatch.setenv(logging_config.LOG_FORMAT_ENV, "json")
    logging_config.configure_logging()
    handler_count_before = len(logging.getLogger().handlers)
    logging_config.configure_logging()
    logging_config.configure_logging()
    handler_count_after = len(logging.getLogger().handlers)
    assert handler_count_before == handler_count_after == 1


def test_run_id_propagates_into_event_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    """v1.1.0 Task 14: every RunEvent the orchestrator surfaces must
    carry the bound ``run_id`` so downstream subscribers can correlate
    without re-deriving it. We mock _run_full_impl to capture the
    emit() wrapper the orchestrator hands down.
    """
    from yt_uniquifier.core import orchestrator
    from yt_uniquifier.core.runner import RunEvent

    captured: list[RunEvent] = []

    def fake_impl(plan, options, emit, cancel_token, pause_token):  # type: ignore[no-untyped-def]
        # Push an event with no run_id; the wrapper must inject it.
        emit(RunEvent(kind="log", payload={"phase": "test", "message": "hi"}))
        # An event that already has a run_id (e.g. from a callee that
        # bound its own) must NOT be overwritten.
        emit(RunEvent(kind="log", payload={"run_id": "preset", "n": 1}))
        return orchestrator.RunSummary(
            output=options.output, plan=plan, segments_done=0,
            preflight_findings=[],
        )

    monkeypatch.setattr(orchestrator, "_run_full_impl", fake_impl)

    # Also short-circuit notification + telemetry to keep the test
    # hermetic (they're tested independently).
    monkeypatch.setattr(
        orchestrator, "_maybe_dispatch_notification",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        orchestrator, "_maybe_record_telemetry",
        lambda *a, **kw: None,
    )

    from yt_uniquifier.core.models import (
        AudioStream,
        EncoderCandidate,
        HDRInfo,
        Plan,
        Profile,
        SourceMeta,
        VideoStream,
    )
    from yt_uniquifier.core.pipeline import compute_plan_hash

    src_path = tmp_path / "x.mp4"  # type: ignore[attr-defined]
    src_path.touch()
    src = SourceMeta(
        path=src_path, container="mp4", duration_sec=1.0, size_bytes=10,
        video=[VideoStream(
            index=0, codec="h264", width=128, height=72, fps=24.0,
            duration_sec=1.0, pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    profile = Profile(name="t", transforms=[])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(
        source=src, profile=profile, encoder=enc,
        plan_hash=compute_plan_hash(src, profile, enc),
    )
    options = orchestrator.RunOptions(
        work_dir=tmp_path / "work",  # type: ignore[operator]
        output=tmp_path / "out.mp4",  # type: ignore[operator]
    )
    summary = orchestrator.run_full(
        plan, options, on_event=captured.append,
    )

    # The orchestrator auto-fills run_id when the caller didn't supply
    # one — every captured event must now carry it.
    auto_id = summary.plan.plan_hash  # any non-empty string is fine
    del auto_id
    assert captured, "expected at least one captured event"
    first = captured[0]
    assert "run_id" in first.payload
    assert first.payload["run_id"], "run_id must be non-empty"
    # The pre-set value on the second event survives unchanged.
    second = captured[1]
    assert second.payload["run_id"] == "preset"


def test_run_id_caller_supplied_value_is_respected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    """Web layer passes its own run_id so the HTTP response, the SSE
    stream, and the orchestrator's structured log all share one ID.
    """
    from yt_uniquifier.core import orchestrator
    from yt_uniquifier.core.runner import RunEvent

    captured: list[RunEvent] = []

    def fake_impl(plan, options, emit, cancel_token, pause_token):  # type: ignore[no-untyped-def]
        emit(RunEvent(kind="log", payload={"phase": "x"}))
        return orchestrator.RunSummary(
            output=options.output, plan=plan, segments_done=0,
            preflight_findings=[],
        )

    monkeypatch.setattr(orchestrator, "_run_full_impl", fake_impl)
    monkeypatch.setattr(orchestrator, "_maybe_dispatch_notification", lambda *a, **kw: None)
    monkeypatch.setattr(orchestrator, "_maybe_record_telemetry", lambda *a, **kw: None)

    from yt_uniquifier.core.models import (
        AudioStream,
        EncoderCandidate,
        HDRInfo,
        Plan,
        Profile,
        SourceMeta,
        VideoStream,
    )
    from yt_uniquifier.core.pipeline import compute_plan_hash

    src_path = tmp_path / "x.mp4"  # type: ignore[attr-defined]
    src_path.touch()
    src = SourceMeta(
        path=src_path, container="mp4", duration_sec=1.0, size_bytes=10,
        video=[VideoStream(
            index=0, codec="h264", width=128, height=72, fps=24.0,
            duration_sec=1.0, pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    profile = Profile(name="t", transforms=[])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(
        source=src, profile=profile, encoder=enc,
        plan_hash=compute_plan_hash(src, profile, enc),
    )
    explicit_id = "web-supplied-run-id-deadbeef"
    options = orchestrator.RunOptions(
        work_dir=tmp_path / "work",  # type: ignore[operator]
        output=tmp_path / "out.mp4",  # type: ignore[operator]
        run_id=explicit_id,
    )
    orchestrator.run_full(plan, options, on_event=captured.append)

    assert captured
    assert captured[0].payload["run_id"] == explicit_id
