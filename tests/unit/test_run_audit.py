"""v1.3.0 Task 32 — run-level audit log unit tests.

Covers:

  * record_run with a real path writes one JSONL line
  * None path → no-op (no file created)
  * principal defaults via YT_UNIQ_AUDIT_PRINCIPAL env var
  * resolve_audit_log_path honours explicit > env > None
  * sha256_file matches hashlib
  * write error logged at WARN, never raised
  * concurrent writers serialise (no torn line)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
from pathlib import Path

import pytest

from yt_uniquifier.core import audit


def _now() -> dt.datetime:
    return dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.UTC)


def test_record_run_writes_one_jsonl_line(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    input_p = tmp_path / "in.mp4"
    input_p.write_bytes(b"hello world")
    audit.record_run(
        audit_log_path=log_path,
        run_id="r-1",
        started_at=_now(),
        ended_at=_now(),
        input_path=input_p,
        output_path=tmp_path / "out.mp4",
        plan_hash="abc12345",
        result="completed",
        segments_total=5,
        segments_failed=0,
        principal="alice",
    )
    raw = log_path.read_text(encoding="utf-8")
    lines = raw.strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["run_id"] == "r-1"
    assert rec["plan_hash"] == "abc12345"
    assert rec["result"] == "completed"
    assert rec["segments_total"] == 5
    assert rec["principal"] == "alice"
    assert rec["event"] == "run.completed"
    assert rec["input_sha256"] == hashlib.sha256(b"hello world").hexdigest()


def test_record_run_failed_uses_run_failed_event(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    input_p = tmp_path / "in.mp4"
    input_p.touch()
    audit.record_run(
        audit_log_path=log_path,
        run_id="r-2", started_at=_now(), ended_at=_now(),
        input_path=input_p, output_path=tmp_path / "out.mp4",
        plan_hash="h", result="failed",
        segments_total=0, segments_failed=2,
        extra={"error_type": "PipelineError"},
    )
    rec = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert rec["event"] == "run.failed"
    assert rec["segments_failed"] == 2
    assert rec["extra"] == {"error_type": "PipelineError"}


def test_record_run_none_path_is_noop(tmp_path: Path) -> None:
    audit.record_run(
        audit_log_path=None,
        run_id="r", started_at=_now(), ended_at=_now(),
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        plan_hash="h", result="completed",
        segments_total=0, segments_failed=0,
    )
    assert list(tmp_path.iterdir()) == []


def test_principal_falls_back_to_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("YT_UNIQ_AUDIT_PRINCIPAL", "cron-bot")
    log_path = tmp_path / "audit.jsonl"
    input_p = tmp_path / "in.mp4"
    input_p.touch()
    audit.record_run(
        audit_log_path=log_path,
        run_id="r", started_at=_now(), ended_at=_now(),
        input_path=input_p, output_path=tmp_path / "out.mp4",
        plan_hash="h", result="completed",
        segments_total=0, segments_failed=0,
    )
    rec = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert rec["principal"] == "cron-bot"


def test_resolve_audit_log_path_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Explicit > env > None."""
    monkeypatch.setenv("YT_UNIQ_AUDIT_LOG", str(tmp_path / "from_env.jsonl"))
    explicit = tmp_path / "explicit.jsonl"
    assert audit.resolve_audit_log_path(explicit) == explicit
    # Explicit None falls through to env.
    assert audit.resolve_audit_log_path(None) == tmp_path / "from_env.jsonl"
    # Env unset → None.
    monkeypatch.delenv("YT_UNIQ_AUDIT_LOG")
    assert audit.resolve_audit_log_path(None) is None


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"the quick brown fox" * 50_000
    p = tmp_path / "f.bin"
    p.write_bytes(payload)
    assert audit.sha256_file(p) == hashlib.sha256(payload).hexdigest()


def test_write_failure_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A read-only directory must not crash the run — audit failure
    is logged at WARN and execution continues."""
    log_path = tmp_path / "nonexistent_dir" / "audit.jsonl"
    # Force open() to fail by making the parent dir non-creatable.
    real_open = Path.open

    def fake_open(self: Path, *a: object, **kw: object) -> object:
        if self == log_path:
            raise OSError("read-only disk")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", fake_open)
    # mkdir is fine; we want the open to fail.
    caplog.set_level(logging.WARNING, logger="yt_uniquifier.core.audit")
    audit.record_run(
        audit_log_path=log_path,
        run_id="r", started_at=_now(), ended_at=_now(),
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        plan_hash="h", result="completed",
        segments_total=0, segments_failed=0,
    )
    assert any("audit log write failed" in r.message for r in caplog.records)


def test_concurrent_writers_produce_clean_lines(tmp_path: Path) -> None:
    """Two threads racing on the same log file must produce two valid
    JSON lines (kernel append semantics + the in-process RLock)."""
    log_path = tmp_path / "audit.jsonl"
    input_p = tmp_path / "in.mp4"
    input_p.write_bytes(b"x" * 1024)

    def writer(run_id: str) -> None:
        audit.record_run(
            audit_log_path=log_path,
            run_id=run_id, started_at=_now(), ended_at=_now(),
            input_path=input_p, output_path=tmp_path / "out.mp4",
            plan_hash="h", result="completed",
            segments_total=0, segments_failed=0,
        )

    threads = [threading.Thread(target=writer, args=(f"r-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8
    for line in lines:
        rec = json.loads(line)
        assert rec["run_id"].startswith("r-")
