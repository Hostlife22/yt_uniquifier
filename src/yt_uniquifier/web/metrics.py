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
    labelnames=("run_id",),
)


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

    if kind == "segment_done":
        status = str(payload.get("status", "done"))
        SEGMENTS_TOTAL.labels(status=status).inc()
        duration = payload.get("duration_sec")
        if isinstance(duration, (int, float)) and duration >= 0:
            SEGMENT_DURATION_SECONDS.observe(float(duration))
    elif kind == "error":
        encoder = str(payload.get("encoder") or "unknown")
        FFMPEG_FAILURES_TOTAL.labels(encoder=encoder).inc()
        SEGMENTS_TOTAL.labels(status="failed").inc()
    elif kind == "divergence_sample":
        rid = str(payload.get("run_id") or "unknown")
        val = payload.get("phash_distance")
        if isinstance(val, (int, float)):
            PHASH_DIVERGENCE_LAST.labels(run_id=rid).set(float(val))


def observe_run_terminal(result: str, wall_clock_sec: float) -> None:
    """Record run-level outcome + wall-clock. Called once per run by
    the web layer's worker thread on success / cancel / failure.
    """
    RUNS_TOTAL.labels(result=result).inc()
    if wall_clock_sec >= 0:
        RUN_DURATION_SECONDS.observe(wall_clock_sec)
