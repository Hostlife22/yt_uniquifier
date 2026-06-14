"""Opt-in local telemetry (v0.9.0 R3).

The trust contract:

* **Off by default.** A fresh install never records anything until the
  user opts in (either via the GUI first-run dialog or by editing the
  Profile/RunOptions to attach a ``TelemetryConfig``).
* **Local only.** v0.9 writes append-only JSONL to a per-user dir
  under ``~/.local/share`` (Linux) / ``~/Library/Application Support``
  (macOS) / ``%APPDATA%`` (Windows). No network egress, period. A
  future v1.0 may add explicit upload behind a second consent layer.
* **Path-redacting.** Absolute paths are rewritten to ``<HOME>/…``
  before being recorded so a shared export does not leak the user's
  filesystem layout. Disabled per-event when the caller explicitly
  asks (e.g. an internal debug dump).
* **Schema-stable.** Every event carries ``schema_version`` so a
  downstream analyser can refuse to interpret an unknown shape rather
  than guess.

Concurrency: ``record`` writes are protected by a process-local
``threading.RLock``. Two parallel orchestrators on the same machine
serialise through the lock; cross-process writers append to the same
file under O_APPEND which the OS makes atomic for writes ≤ PIPE_BUF
on POSIX (every event is well under 4 KiB).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yt_uniquifier.core.errors import YtUniquifierError

_log = logging.getLogger(__name__)

# Bump on any breaking change to the event shape (added optional
# fields don't count; removed/renamed fields do).
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TelemetryError(YtUniquifierError):
    """Telemetry I/O failed."""


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------


def default_events_dir() -> Path:
    """Per-user telemetry directory.

    Picks a per-platform sensible default that survives uninstall +
    reinstall. The GUI honours ``QStandardPaths.AppDataLocation``
    which lands at the same place on every platform we ship for, so
    CLI and GUI exports come from the same JSONL file.
    """
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support"
                / "yt_uniquifier" / "telemetry")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "yt_uniquifier" / "telemetry"
        return Path.home() / "AppData" / "Roaming" / "yt_uniquifier" / "telemetry"
    # Linux + BSD + every other POSIX: XDG_DATA_HOME or ~/.local/share.
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "yt_uniquifier" / "telemetry"


def default_consent_marker() -> Path:
    """File whose presence proves the user has answered the consent prompt.

    Body is irrelevant; existence alone gates the first-run dialog so
    the GUI doesn't re-prompt every launch. The opt-in *decision*
    lives in the persisted config (TelemetryConfig.enabled).
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "yt_uniquifier" / "telemetry-consent"
    return Path.home() / ".config" / "yt_uniquifier" / "telemetry-consent"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TelemetryConfig(BaseModel):
    """Top-level config attached to ``RunOptions.telemetry``.

    All fields default to the safe choice — ``enabled=False`` means a
    user who never opens Settings never has anything recorded, even if
    a profile or workflow accidentally passes a TelemetryConfig
    through.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    redact_paths: bool = True
    events_dir: Path | None = None  # None ⇒ default_events_dir()
    # Bound on the JSONL file size before rotation. Mirrors the crash.log
    # rotation in gui/app_pyqt.py (E6). 1 MiB holds ~10k summary
    # events; older rolls to events.jsonl.1.
    rotate_at_bytes: int = Field(default=1 * 1024 * 1024, ge=4096, le=64 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Path redaction
# ---------------------------------------------------------------------------


_HOME_STR = str(Path.home())


def redact_path(value: str) -> str:
    """Replace the current user's home prefix with ``<HOME>``.

    Operates on strings rather than Paths so the caller can pass JSON
    values without coercing. A best-effort match; relative paths and
    paths that don't start with ``$HOME`` are returned unchanged.

    Separator tolerance: on Windows the OS sep is ``\\`` but cross-
    platform code that stamps paths via ``Path.as_posix()`` or
    ``str(path).replace("\\", "/")`` produces forward slashes. We
    accept BOTH so a path of the shape ``C:\\Users\\foo/Movies/x``
    (Windows home + POSIX-style child) still matches.
    """
    if not value:
        return value
    # Use the longest-prefix match so ``/Users/foo`` doesn't shadow
    # ``/Users/foobar`` when one user is a substring of another.
    if value == _HOME_STR:
        return "<HOME>"
    # Try each plausible separator. ``set`` dedups when os.sep is "/"
    # (POSIX) so we don't double-check the same prefix.
    for sep in {os.sep, "/"}:
        prefix = _HOME_STR + sep
        if value.startswith(prefix):
            return "<HOME>" + value[len(_HOME_STR):]
    return value


def redact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Walk ``event`` and replace any string value that looks path-like.

    Only operates on top-level + one-level-nested string values; we
    deliberately do NOT recurse arbitrarily so a malicious event can't
    burn CPU here by nesting 1000 levels deep.
    """
    out: dict[str, Any] = {}
    for k, v in event.items():
        if isinstance(v, str):
            out[k] = redact_path(v)
        elif isinstance(v, dict):
            out[k] = {
                kk: (redact_path(vv) if isinstance(vv, str) else vv)
                for kk, vv in v.items()
            }
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


_EVENTS_FILENAME = "events.jsonl"
_write_lock = threading.RLock()


def _events_path(config: TelemetryConfig) -> Path:
    return (config.events_dir or default_events_dir()) / _EVENTS_FILENAME


def _rotate_if_needed(path: Path, limit: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < limit:
        return
    backup = path.with_name(path.name + ".1")
    try:
        if backup.exists():
            backup.unlink()
        path.replace(backup)
    except OSError as exc:
        _log.warning("could not rotate telemetry log %s: %s", path, exc)


def record(event: dict[str, Any], config: TelemetryConfig) -> None:
    """Append one event to the JSONL log, or no-op if disabled.

    ``event`` is augmented with ``ts``, ``event_id``, ``schema_version``.
    A failure to write is logged and swallowed — telemetry must never
    fail an encode. The caller's invariants do not depend on the
    write succeeding.
    """
    if not config.enabled:
        return
    path = _events_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = dict(event)
        if config.redact_paths:
            body = redact_event(body)
        body.setdefault("schema_version", SCHEMA_VERSION)
        body.setdefault("event_id", str(uuid.uuid4()))
        body.setdefault("ts", time.time())
        encoded = json.dumps(body, ensure_ascii=False, default=str)
        if "\n" in encoded:
            # JSONL line-integrity contract — embedded newlines would
            # split one event across two physical lines. ``json.dumps``
            # without ``indent`` should never produce them; assert
            # defensively because losing the contract corrupts every
            # downstream parser.
            encoded = encoded.replace("\n", " ")
        with _write_lock:
            _rotate_if_needed(path, config.rotate_at_bytes)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(encoded + "\n")
                fh.flush()
    except OSError as exc:
        _log.warning("telemetry write failed: %s", exc)


# ---------------------------------------------------------------------------
# Reader (CLI export / status / GUI viewer)
# ---------------------------------------------------------------------------


def iter_events(events_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    """Yield parsed events from the JSONL file (and the .1 backup if any).

    Malformed lines are skipped with a warning rather than raising —
    an export should produce as much as the file actually contains,
    even after a power-loss-truncated line.
    """
    root = events_dir or default_events_dir()
    path = root / _EVENTS_FILENAME
    backup = path.with_name(path.name + ".1")
    for candidate in (backup, path):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                for lineno, raw in enumerate(fh, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError as exc:
                        _log.warning(
                            "skipping malformed telemetry line %s:%d: %s",
                            candidate, lineno, exc,
                        )
        except OSError as exc:
            _log.warning("could not read %s: %s", candidate, exc)


def event_count(events_dir: Path | None = None) -> int:
    """Cheap line count for the GUI / CLI status display."""
    total = 0
    for _ in iter_events(events_dir):
        total += 1
    return total


def export_events(dest: Path, events_dir: Path | None = None) -> int:
    """Copy events into ``dest`` as a sortable JSONL file; returns count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with dest.open("w", encoding="utf-8") as out:
        for event in iter_events(events_dir):
            out.write(json.dumps(event, ensure_ascii=False) + "\n")
            count += 1
    return count


def purge_events(events_dir: Path | None = None) -> None:
    """Wipe the telemetry directory. Safe to call when nothing exists."""
    root = events_dir or default_events_dir()
    if root.exists():
        shutil.rmtree(root)


# ---------------------------------------------------------------------------
# Consent helpers (GUI uses these; CLI exposes them via cmd_telemetry)
# ---------------------------------------------------------------------------


def has_consent_marker(path: Path | None = None) -> bool:
    return (path or default_consent_marker()).exists()


def write_consent_marker(decision: bool, path: Path | None = None) -> None:
    """Persist the fact that the user answered the prompt.

    The marker itself contains only the decision string so a support
    technician inspecting the dir can tell what the user picked
    without parsing a config file.
    """
    target = path or default_consent_marker()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("enabled" if decision else "disabled", encoding="utf-8")
