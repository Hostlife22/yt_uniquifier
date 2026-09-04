"""Prometheus metrics for the web UI (v1.1.0 Task 15).

Counters / histograms / gauges are owned at module scope so re-running
``build_app`` (e.g. in tests) reuses the same registry — Prometheus
rejects duplicate registration. ``update_from_event`` plumbs
``RunEvent`` payloads into the right family without coupling the
orchestrator to the metrics module: web/routes/run.py wires it via
the ``on_event`` callback it already builds for SSE.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Gauge, Histogram

# Counters --------------------------------------------------------------

SEGMENTS_TOTAL = Counter(
    "yt_uniq_segments_total",
    "Number of segments processed, partitioned by terminal status.",
    labelnames=("status",),
)

FFMPEG_FAILURES_TOTAL = Counter(
    "yt_uniq_ffmpeg_failures_total",
    "ffmpeg subprocess non-zero exits, partitioned by encoder name.",
    labelnames=("encoder",),
)

RUNS_TOTAL = Counter(
    "yt_uniq_runs_total",
    "Run-level outcomes, partitioned by result (completed/failed/cancelled).",
    labelnames=("result",),
)

RUN_STATE_EVENTS_TOTAL = Counter(
    "yt_uniq_run_state_events_total",
    "Run lifecycle transitions. Labels are a fixed bounded state vocabulary.",
    labelnames=("state",),
)

RUN_STATES = (
    "queued",
    "active",
    "failed",
    "cancelled",
    "resumed",
    "completed",
)

_ENCODER_LABELS = {
    "libx264",
    "libx265",
    "libaom-av1",
    "h264_nvenc",
    "hevc_nvenc",
    "av1_nvenc",
    "h264_qsv",
    "hevc_qsv",
    "av1_qsv",
    "h264_amf",
    "hevc_amf",
    "av1_amf",
    "h264_videotoolbox",
    "hevc_videotoolbox",
    "unknown",
}

# Histograms ------------------------------------------------------------

SEGMENT_DURATION_SECONDS = Histogram(
    "yt_uniq_segment_duration_seconds",
    "Wall-clock seconds per segment encode (incl. filter graph).",
    # Wider bucket spread than the default — segment durations on real
    # workloads span 1 s (smoke tests) to 1 hour (very long inputs on
    # CPU encoders).
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
)

RUN_DURATION_SECONDS = Histogram(
    "yt_uniq_run_duration_seconds",
    "Wall-clock seconds from run.started to terminal event.",
    buckets=(10, 30, 60, 300, 900, 1800, 3600, 7200, 21600, 43200),
)

# Gauges ----------------------------------------------------------------

ACTIVE_RUNS = Gauge(
    "yt_uniq_active_runs",
    "Currently in-flight runs (pending + running). Updated on every "
    "POST /api/run and on every terminal status transition.",
)

PHASH_DIVERGENCE_LAST = Gauge(
    "yt_uniq_phash_divergence_last",
    "Last observed pHash divergence sample (0..64; higher = more diverged).",
)

# Materialise every bounded state at import time so a fresh /metrics scrape is
# operationally useful before the first run reaches that state.
for _state in RUN_STATES:
    RUN_STATE_EVENTS_TOTAL.labels(state=_state)


def update_from_event(event: Any) -> None:
    """Plumb a ``RunEvent`` into the relevant metric families.

    Defensive: an event with unexpected shape is silently ignored so a
    one-off payload key doesn't crash a long-running run. Each branch
    keys off the documented ``RunEvent.kind`` values; new kinds simply
    fall through.
    """
    kind = getattr(event, "kind", None)
    payload = getattr(event, "payload", {}) or {}
    if not isinstance(payload, dict):
        return

    if kind == "segment_done" or (
        kind == "log"
        and payload.get("phase") == "segment"
        and payload.get("status") == "done"
    ):
        status = str(payload.get("status", "done"))
        SEGMENTS_TOTAL.labels(status=status).inc()
        duration = payload.get("duration_sec")
        if isinstance(duration, (int, float)) and duration >= 0:
            SEGMENT_DURATION_SECONDS.observe(float(duration))
    elif kind == "error":
        candidate = str(payload.get("encoder") or "unknown")
        # Labels are public and cardinality-sensitive. Never copy arbitrary
        # event/path/token data into the Prometheus label set.
        encoder = candidate if candidate in _ENCODER_LABELS else "unknown"
        if "encoder" in payload:
            FFMPEG_FAILURES_TOTAL.labels(encoder=encoder).inc()
        segment = payload.get("segment")
        if isinstance(segment, int) and not isinstance(segment, bool):
            SEGMENTS_TOTAL.labels(status="failed").inc()
    elif kind == "divergence_sample":
        val = payload.get("phash_distance")
        if isinstance(val, (int, float)):
            # Never label metrics by run/job/path/token: those values are
            # unbounded and may be sensitive. Correlation stays in events/logs.
            PHASH_DIVERGENCE_LAST.set(float(val))
    elif (
        kind == "log"
        and payload.get("phase") == "plan"
        and payload.get("resumed") is True
    ):
        observe_run_state("resumed")


def observe_run_state(state: str) -> None:
    """Increment one bounded lifecycle transition counter."""
    if state not in RUN_STATES:
        raise ValueError(f"unsupported run state: {state!r}")
    RUN_STATE_EVENTS_TOTAL.labels(state=state).inc()


def observe_run_terminal(result: str, wall_clock_sec: float) -> None:
    """Record run-level outcome + wall-clock. Called once per run by
    the web layer's worker thread on success / cancel / failure.
    """
    if result not in {"completed", "failed", "cancelled"}:
        raise ValueError(f"unsupported terminal result: {result!r}")
    observe_run_state(result)
    RUNS_TOTAL.labels(result=result).inc()
    if wall_clock_sec >= 0:
        RUN_DURATION_SECONDS.observe(wall_clock_sec)
