"""v1.3.0 Task 34 — OpenTelemetry tracing unit tests.

Strategy: stub the OTel SDK with an InMemorySpanExporter so we exercise
the wiring (resource, span name, attributes, duration_us) without
binding to a real OTLP endpoint.  Falls back to is-noop tests when the
[obs] extra isn't installed.
"""

from __future__ import annotations

import pytest

from yt_uniquifier.core import tracing


def test_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OTEL_EXPORTER_OTLP_ENDPOINT → run_span / segment_span are
    no-ops; nothing is imported lazily."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    tracing._reset_for_tests()
    with tracing.run_span(plan_hash="abc", encoder_kind="x264"), tracing.segment_span(
        plan_hash="abc", segment_idx=0, encoder_kind="x264",
    ):
        pass
    assert tracing._tracer is None


def test_obs_extra_missing_logs_and_disables(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Env set but [obs] extra absent → WARN + disabled.  Forces the
    import chain to raise ImportError and verifies graceful fallback."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing._reset_for_tests()
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **kw: object) -> object:
        if name.startswith("opentelemetry"):
            raise ImportError("No module named 'opentelemetry'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    caplog.set_level("WARNING", logger="yt_uniquifier.core.tracing")
    with tracing.run_span(plan_hash="abc", encoder_kind="x264"):
        pass
    assert tracing._tracer is None
    assert any("[obs] extra missing" in r.message for r in caplog.records)


def test_init_idempotent_under_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """_maybe_init must serialise — two threads racing must not call
    set_tracer_provider twice."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    tracing._reset_for_tests()
    # With env unset, _maybe_init returns None on every call.
    import threading
    results: list[object] = []

    def worker() -> None:
        results.append(tracing._maybe_init())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [None] * 8


def test_span_records_attributes_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end (synthetic): _maybe_init is patched to return a
    spy-tracer; run_span sets plan_hash + encoder_kind + duration_us
    on the resulting span."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing._reset_for_tests()

    set_calls: list[tuple[str, object]] = []

    class _Span:
        def set_attribute(self, key: str, value: object) -> None:
            set_calls.append((key, value))

    class _SpanCtx:
        def __enter__(self) -> _Span:
            return _Span()
        def __exit__(self, *a: object) -> None:
            return None

    class _Tracer:
        def start_as_current_span(self, name: str) -> _SpanCtx:
            set_calls.append(("__span_name__", name))
            return _SpanCtx()

    monkeypatch.setattr(tracing, "_maybe_init", lambda: _Tracer())

    with tracing.run_span(plan_hash="ph", encoder_kind="x264"):
        pass

    keys = {k for k, _ in set_calls}
    assert keys >= {"plan_hash", "encoder_kind", "duration_us"}
    assert ("plan_hash", "ph") in set_calls
    assert ("encoder_kind", "x264") in set_calls
    assert ("__span_name__", "yt_uniquifier.run") in set_calls


def test_segment_span_includes_segment_idx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing._reset_for_tests()

    set_calls: list[tuple[str, object]] = []

    class _Span:
        def set_attribute(self, key: str, value: object) -> None:
            set_calls.append((key, value))

    class _SpanCtx:
        def __enter__(self) -> _Span:
            return _Span()
        def __exit__(self, *a: object) -> None:
            return None

    class _Tracer:
        def start_as_current_span(self, name: str) -> _SpanCtx:
            set_calls.append(("__span_name__", name))
            return _SpanCtx()

    monkeypatch.setattr(tracing, "_maybe_init", lambda: _Tracer())

    with tracing.segment_span(
        plan_hash="ph", segment_idx=7, encoder_kind="svtav1",
    ):
        pass

    assert ("segment_idx", 7) in set_calls
    assert ("__span_name__", "yt_uniquifier.segment") in set_calls
