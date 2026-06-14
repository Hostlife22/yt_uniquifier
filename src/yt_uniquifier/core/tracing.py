"""v1.3.0 Task 34 — opt-in OpenTelemetry tracing.

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set in the environment AND the
``[obs]`` extra is installed, ``segment_span`` and ``run_span`` produce
OTLP spans with attributes:

  * ``plan_hash``       — resume key
  * ``segment_idx``     — per-segment span only
  * ``encoder_kind``    — vendor tag (x264, nvenc, svtav1, …)
  * ``duration_us``     — recorded by ``time.monotonic_ns`` so re-entry
                          under suspend doesn't blow the timing

When either prerequisite is missing the helpers degrade to a no-op
context manager — orchestrator code stays the same regardless of
deployment.  Initialisation is lazy: the first ``run_span`` call
configures the global tracer provider; subsequent calls reuse it.
Idempotent via a process-level lock so the GUI's worker pool can't
race-init two providers.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Generator
from typing import Any

_log = logging.getLogger(__name__)
_INIT_LOCK = threading.Lock()
_initialised = False
_tracer: Any = None


def _is_enabled() -> bool:
    """True iff the operator opted-in via OTEL_EXPORTER_OTLP_ENDPOINT."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _maybe_init() -> Any:
    """Configure the global tracer provider once.  Returns the tracer
    or ``None`` when tracing is disabled / SDK missing."""
    global _initialised, _tracer
    if _initialised:
        return _tracer
    with _INIT_LOCK:
        if _initialised:
            return _tracer
        _initialised = True
        if not _is_enabled():
            return None
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            _log.warning(
                "OTEL endpoint set but [obs] extra missing (%s); "
                "tracing disabled.  Install with `pip install "
                "yt-uniquifier[obs]`.",
                exc,
            )
            return None
        service = os.environ.get("OTEL_SERVICE_NAME", "yt-uniquifier")
        provider = TracerProvider(resource=Resource.create({
            "service.name": service,
        }))
        exporter = OTLPSpanExporter()  # endpoint pulled from env var
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("yt_uniquifier.core")
        return _tracer


@contextlib.contextmanager
def run_span(*, plan_hash: str, encoder_kind: str) -> Generator[None, None, None]:
    """Outer run span — wraps the entire run_full call.

    No-op when tracing isn't enabled; the orchestrator's normal
    instrumentation (structlog + telemetry + audit) keeps working.
    """
    tracer = _maybe_init()
    if tracer is None:
        yield
        return
    started_ns = time.monotonic_ns()
    with tracer.start_as_current_span("yt_uniquifier.run") as span:
        span.set_attribute("plan_hash", plan_hash)
        span.set_attribute("encoder_kind", encoder_kind)
        try:
            yield
        finally:
            span.set_attribute(
                "duration_us",
                (time.monotonic_ns() - started_ns) // 1_000,
            )


@contextlib.contextmanager
def segment_span(
    *, plan_hash: str, segment_idx: int, encoder_kind: str,
) -> Generator[None, None, None]:
    """Per-segment span — wraps one encode invocation."""
    tracer = _maybe_init()
    if tracer is None:
        yield
        return
    started_ns = time.monotonic_ns()
    with tracer.start_as_current_span("yt_uniquifier.segment") as span:
        span.set_attribute("plan_hash", plan_hash)
        span.set_attribute("segment_idx", segment_idx)
        span.set_attribute("encoder_kind", encoder_kind)
        try:
            yield
        finally:
            span.set_attribute(
                "duration_us",
                (time.monotonic_ns() - started_ns) // 1_000,
            )


def _reset_for_tests() -> None:
    """Re-arm lazy init.  Only the test suite should call this."""
    global _initialised, _tracer
    with _INIT_LOCK:
        _initialised = False
        _tracer = None
