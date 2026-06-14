"""Append-only audit log for state-changing web requests (v1.1.0 Task 16).

Each state-changing endpoint (POST /api/run, POST /api/run/{id}/cancel)
calls ``audit(event, principal, payload)`` which appends a single
JSONL line to ``WebConfig.audit_log_path``. The file is operator-
managed; no rotation here so logs are easy to tail with stock tools
and easy to ship into ELK / Loki / S3.

Append-only and atomic: each line is JSON-encoded with
``json.dumps`` and written through a process-level RLock; the
underlying file descriptor is opened in ``"a"`` mode so concurrent
processes are serialised by the kernel's append semantics. No log
file = no-op (so unit tests that don't opt-in don't write to disk).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_LOCK = threading.RLock()


def audit(
    event: str,
    *,
    principal: str | None,
    payload: dict[str, Any] | None,
    audit_log_path: Path | None,
) -> None:
    """Append one JSON line describing a state-changing request.

    ``event`` is a stable short name (``api.run.start``,
    ``api.run.cancel``). ``principal`` is the authenticated user, or
    ``None`` for anonymous LAN-trusted deployments. ``payload`` is the
    sanitised request context — never raw secrets. ``audit_log_path``
    of ``None`` makes this a no-op so unit tests opt-in.
    """
    if audit_log_path is None:
        return
    record: dict[str, Any] = {
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "event": event,
        "principal": principal,
        "payload": payload or {},
    }
    line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError as exc:
        # Audit failure is never fatal — the request itself may still
        # be the right thing to do. Surface it loudly in app logs so
        # operators notice a misconfigured path / read-only disk.
        _log.error("audit log write failed (path=%s): %s", audit_log_path, exc)
