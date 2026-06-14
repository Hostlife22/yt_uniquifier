"""v1.3.0 Task 32 — run-level audit log.

One JSONL entry per run summarising:

  * ``run_id`` — uuid7 correlation ID (matches structlog binding)
  * ``started_at`` / ``ended_at`` — ISO-8601 UTC
  * ``principal`` — operator label (web layer's authenticated user, or
    the value of ``YT_UNIQ_AUDIT_PRINCIPAL`` for CLI runs)
  * ``input_sha256`` — content hash of the source file
  * ``plan_hash`` — resume key (same value the orchestrator uses)
  * ``output_path`` — final mp4 location
  * ``result`` — ``"completed"`` | ``"failed"``
  * ``segments_total`` / ``segments_failed`` — concat-time accounting

Append-only (kernel-serialised through ``"a"`` mode + a process-level
RLock), no rotation — operators wire the file into their own log
shipper (Logstash, Vector, S3 putter).  Default disabled: ``None``
log path makes ``record_run`` a no-op so CLI users don't accidentally
accumulate JSONL on a single-user laptop.

The web layer's request-level audit (``web/audit.py``, v1.1.0 Task 16)
runs alongside this — a web-triggered run produces both a
``api.run.start`` request entry AND a ``run.completed`` run-level
entry, joined by the shared ``run_id``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)
_LOCK = threading.RLock()

RunResult = Literal["completed", "failed"]


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file (1 MiB chunks)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record_run(
    *,
    audit_log_path: Path | None,
    run_id: str,
    started_at: _dt.datetime,
    ended_at: _dt.datetime,
    input_path: Path,
    output_path: Path,
    plan_hash: str,
    result: RunResult,
    segments_total: int,
    segments_failed: int,
    principal: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one run-summary JSONL line.

    A ``None`` ``audit_log_path`` short-circuits — callers opt-in by
    setting ``RunOptions.audit_log_path`` or the env-var fallback
    ``YT_UNIQ_AUDIT_LOG``.

    ``principal`` defaults to ``YT_UNIQ_AUDIT_PRINCIPAL`` env var when
    unset, so CLI users on shared boxes can stamp every run with their
    own label without piping through every options builder.

    Input SHA is computed lazily inside the write critical section.
    A streaming hash on a 10 GB source takes ~30 s on a typical box;
    operators who don't want the wait should leave ``audit_log_path``
    unset (the typical CLI default).
    """
    if audit_log_path is None:
        return
    if principal is None:
        principal = os.environ.get("YT_UNIQ_AUDIT_PRINCIPAL")
    try:
        input_sha = sha256_file(input_path) if input_path.exists() else None
    except OSError as exc:
        _log.warning("audit: input_sha256 hash failed (%s)", exc)
        input_sha = None
    record: dict[str, Any] = {
        "ts": ended_at.astimezone(_dt.UTC).isoformat(),
        "event": "run.completed" if result == "completed" else "run.failed",
        "run_id": run_id,
        "principal": principal,
        "started_at": started_at.astimezone(_dt.UTC).isoformat(),
        "ended_at": ended_at.astimezone(_dt.UTC).isoformat(),
        "input_path": str(input_path),
        "input_sha256": input_sha,
        "plan_hash": plan_hash,
        "output_path": str(output_path),
        "result": result,
        "segments_total": segments_total,
        "segments_failed": segments_failed,
    }
    if extra:
        record["extra"] = extra
    line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError as exc:
        # Audit failure is never fatal — the run itself succeeded /
        # failed for its own reasons.  Surface at WARN so a misrouted
        # log path doesn't quietly drop entries forever.
        _log.warning(
            "audit log write failed (path=%s): %s", audit_log_path, exc,
        )


def resolve_audit_log_path(explicit: Path | None) -> Path | None:
    """Resolve the audit log path: explicit arg → env var → None.

    ``YT_UNIQ_AUDIT_LOG`` is the canonical CLI override so cron jobs
    can stamp every run without threading a flag through every wrapper.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("YT_UNIQ_AUDIT_LOG")
    return Path(raw) if raw else None
