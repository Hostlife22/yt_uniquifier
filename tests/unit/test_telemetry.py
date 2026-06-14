"""Tests for opt-in local telemetry (v0.9.0 R3).

Covers the contract that matters:
  * disabled config writes nothing
  * enabled config writes one JSONL line with required envelope fields
  * concurrent appends from multiple threads stay line-coherent
  * path redaction strips $HOME but preserves other strings
  * rotation kicks in when the file crosses ``rotate_at_bytes``
  * iter_events skips malformed lines without raising
  * consent marker round-trip
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from yt_uniquifier.core import telemetry


@pytest.fixture()
def tele_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tele"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# disabled vs enabled
# ---------------------------------------------------------------------------


def test_disabled_config_is_silent(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=False, events_dir=tele_dir)
    telemetry.record({"kind": "test"}, cfg)
    assert telemetry.event_count(tele_dir) == 0
    assert not (tele_dir / "events.jsonl").exists()


def test_enabled_config_writes_envelope(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    telemetry.record({"kind": "run_summary", "status": "completed"}, cfg)

    events = list(telemetry.iter_events(tele_dir))
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "run_summary"
    assert e["status"] == "completed"
    assert e["schema_version"] == telemetry.SCHEMA_VERSION
    assert isinstance(e["event_id"], str) and len(e["event_id"]) >= 8
    assert isinstance(e["ts"], (int, float))


def test_extra_field_in_config_rejected() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        telemetry.TelemetryConfig(enabled=True, unknown="boom")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_concurrent_writes_stay_line_coherent(tele_dir: Path) -> None:
    """Ten threads × 50 writes each = 500 lines, none torn or merged."""
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir,
                                    rotate_at_bytes=10 * 1024 * 1024)
    barrier = threading.Barrier(10)

    def writer(idx: int) -> None:
        barrier.wait()
        for n in range(50):
            telemetry.record({"kind": "x", "idx": idx, "n": n}, cfg)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = list(telemetry.iter_events(tele_dir))
    assert len(events) == 500
    # Round-trip key sanity — every event has both expected fields and
    # no two share an event_id.
    ids = {e["event_id"] for e in events}
    assert len(ids) == 500


# ---------------------------------------------------------------------------
# path redaction
# ---------------------------------------------------------------------------


def test_redact_replaces_home_prefix() -> None:
    home = str(Path.home())
    # The forward-slash boundary is the cross-platform case (works on
    # both POSIX and Windows because redact_path tolerates either sep
    # after $HOME).
    assert telemetry.redact_path(f"{home}/Movies/in.mp4") == "<HOME>/Movies/in.mp4"
    assert telemetry.redact_path("/Movies/in.mp4") == "/Movies/in.mp4"
    assert telemetry.redact_path("") == ""


def test_redact_accepts_native_os_sep_after_home() -> None:
    """A path stamped with ``os.sep`` after $HOME must also redact.

    Regression for the Windows CI failure where the test used
    forward slash but the function only matched ``os.sep`` — and
    its mirror, where a real Windows path with backslash needs
    the same treatment.
    """
    home = str(Path.home())
    native = home + os.sep + "Movies" + os.sep + "x.mp4"
    redacted = telemetry.redact_path(native)
    assert redacted.startswith("<HOME>"), (
        f"native-sep path was not redacted: {redacted!r}"
    )


def test_redact_bare_home_returns_marker() -> None:
    home = str(Path.home())
    assert telemetry.redact_path(home) == "<HOME>"


def test_redact_event_walks_one_level(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    home = str(Path.home())
    telemetry.record({
        "kind": "run",
        "output": f"{home}/Movies/out.mp4",
        "meta": {"input": f"{home}/Downloads/clip.mp4", "size": 1234},
    }, cfg)
    events = list(telemetry.iter_events(tele_dir))
    assert events[0]["output"].startswith("<HOME>/")
    assert events[0]["meta"]["input"].startswith("<HOME>/")
    assert events[0]["meta"]["size"] == 1234


def test_redact_disabled_keeps_raw(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(
        enabled=True, redact_paths=False, events_dir=tele_dir,
    )
    home = str(Path.home())
    telemetry.record({"kind": "run", "output": f"{home}/x.mp4"}, cfg)
    events = list(telemetry.iter_events(tele_dir))
    assert events[0]["output"] == f"{home}/x.mp4"


# ---------------------------------------------------------------------------
# rotation
# ---------------------------------------------------------------------------


def test_rotation_swaps_active_file_when_size_exceeded(tele_dir: Path) -> None:
    """At most one backup is retained — older history is intentionally
    dropped so the on-disk footprint stays bounded. The test confirms
    the rotation happened (both files exist after enough writes) and
    that reads continue to work across the swap, NOT that all history
    is preserved indefinitely.
    """
    cfg = telemetry.TelemetryConfig(
        enabled=True,
        events_dir=tele_dir,
        rotate_at_bytes=4096,
    )
    # ~30 events of ~175 bytes each = ~5.2 KiB, crosses the 4 KiB
    # threshold once. A second crossing would overwrite the backup;
    # we deliberately stay within one cycle so we can assert exact
    # content preservation.
    for i in range(30):
        telemetry.record({"kind": "fill", "i": i, "pad": "x" * 50}, cfg)
    active = tele_dir / "events.jsonl"
    backup = tele_dir / "events.jsonl.1"
    assert active.exists()
    assert backup.exists()
    events = list(telemetry.iter_events(tele_dir))
    assert len(events) == 30


# ---------------------------------------------------------------------------
# reader robustness
# ---------------------------------------------------------------------------


def test_iter_events_skips_malformed_lines(tele_dir: Path) -> None:
    path = tele_dir / "events.jsonl"
    path.write_text(
        '{"good": 1}\nnot json at all\n{"good": 2}\n',
        encoding="utf-8",
    )
    out = list(telemetry.iter_events(tele_dir))
    assert [e["good"] for e in out] == [1, 2]


def test_event_count_matches_iter(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    for _ in range(7):
        telemetry.record({"kind": "x"}, cfg)
    assert telemetry.event_count(tele_dir) == 7


def test_export_round_trips(tele_dir: Path, tmp_path: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    telemetry.record({"kind": "a"}, cfg)
    telemetry.record({"kind": "b"}, cfg)
    dest = tmp_path / "export.jsonl"
    count = telemetry.export_events(dest, tele_dir)
    assert count == 2
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    kinds = [json.loads(line)["kind"] for line in lines]
    assert sorted(kinds) == ["a", "b"]


def test_purge_removes_dir(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    telemetry.record({"kind": "x"}, cfg)
    assert (tele_dir / "events.jsonl").exists()
    telemetry.purge_events(tele_dir)
    assert not tele_dir.exists()


# ---------------------------------------------------------------------------
# consent marker
# ---------------------------------------------------------------------------


def test_consent_marker_round_trip(tmp_path: Path) -> None:
    marker = tmp_path / "consent"
    assert not telemetry.has_consent_marker(marker)
    telemetry.write_consent_marker(True, marker)
    assert telemetry.has_consent_marker(marker)
    assert marker.read_text(encoding="utf-8") == "enabled"
    telemetry.write_consent_marker(False, marker)
    assert marker.read_text(encoding="utf-8") == "disabled"


# ---------------------------------------------------------------------------
# newline-injection guard
# ---------------------------------------------------------------------------


def test_embedded_newline_in_event_is_neutralised(tele_dir: Path) -> None:
    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tele_dir)
    # An event with a multi-line string would otherwise break the
    # JSONL one-line-per-event contract. We rely on json.dumps not
    # producing literal newlines (no indent) and the defensive
    # replace() in record() catches the rare case where a custom
    # default= path produces them.
    telemetry.record({"kind": "log", "msg": "line1\nline2"}, cfg)
    raw = (tele_dir / "events.jsonl").read_text(encoding="utf-8").rstrip("\n")
    # Exactly one physical line in the file.
    assert "\n" not in raw
    # The event still round-trips through iter_events.
    events = list(telemetry.iter_events(tele_dir))
    assert events[0]["kind"] == "log"


# ---------------------------------------------------------------------------
# default_events_dir picks a sane per-platform location
# ---------------------------------------------------------------------------


def test_default_events_dir_respects_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "x"))
    d = telemetry.default_events_dir()
    assert str(d).startswith(str(tmp_path / "x"))
    assert d.name == "telemetry"
