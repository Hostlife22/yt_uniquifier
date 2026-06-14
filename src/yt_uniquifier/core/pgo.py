"""v1.2.0 Task 28 — Profile-Guided Optimisation cache.

After every successful run, record the achieved wall-clock per minute
of source, the worker count, and the segment duration that won.  On
the next run with the same ``(source_resolution_bucket, codec,
encoder_kind)`` key, the orchestrator can:

  * pre-pick the worker count that actually scaled on this machine
    (NVENC consumer drivers cap at 3 regardless of CPU count; CPU
    encoders peak at cpu_count/2 minus VRAM contention),
  * pick a segment duration that hit the resume-granularity sweet spot
    without ballooning ffmpeg fork overhead,
  * print a calibrated ETA in ``--dry-run`` mode instead of the
    heuristic guess.

The cache lives at ``~/.cache/yt_uniquifier/pgo.sqlite`` as a tiny
SQLite database (~10 KB after dozens of runs).  Schema is one table
with the key columns + the recorded metrics.  Writes are wrapped in a
short transaction so two concurrent ``yt-uniq batch`` workers can both
record without trampling each other.

Lookup with no data falls back gracefully to ``None`` — the
orchestrator's defaults stay in effect.  This is purely a hint cache;
no orchestration decision DEPENDS on it.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

DEFAULT_PGO_PATH = Path.home() / ".cache" / "yt_uniquifier" / "pgo.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pgo_runs (
    resolution_bucket TEXT NOT NULL,
    codec             TEXT NOT NULL,
    encoder_kind      TEXT NOT NULL,
    workers           INTEGER NOT NULL,
    segment_sec       REAL NOT NULL,
    seconds_per_min   REAL NOT NULL,
    recorded_at       REAL NOT NULL,
    PRIMARY KEY (resolution_bucket, codec, encoder_kind, recorded_at)
);
CREATE INDEX IF NOT EXISTS pgo_runs_lookup
    ON pgo_runs (resolution_bucket, codec, encoder_kind, recorded_at DESC);
"""


@dataclass(frozen=True)
class PgoPrediction:
    """Best-known operating point for a given key."""

    workers: int
    segment_sec: float
    seconds_per_min_of_video: float

    def eta_seconds(self, source_duration_sec: float) -> float:
        """Project total wall-clock seconds for a source of N seconds.

        Linear extrapolation from the recorded ``seconds_per_min``; the
        cache is rebuilt per-run so the prediction adapts to whatever
        the machine is actually doing this week.
        """
        return self.seconds_per_min_of_video * (source_duration_sec / 60.0)


def _bucket_resolution(width: int, height: int) -> str:
    """Coarse resolution bucket so the cache generalises across
    near-equivalent dimensions (1920x1080 vs 1916x1080 vs 1920x1078).
    """
    pixels = width * height
    if pixels >= 3840 * 2160 * 0.9:
        return "4k"
    if pixels >= 1920 * 1080 * 0.9:
        return "1080p"
    if pixels >= 1280 * 720 * 0.9:
        return "720p"
    if pixels >= 640 * 360 * 0.9:
        return "sd"
    return "low"


def _connect(path: Path) -> sqlite3.Connection:
    """Open the sqlite db, creating the parent directory + schema as needed.

    ``isolation_level=None`` uses explicit transactions; we wrap writes
    in BEGIN IMMEDIATE so a concurrent ``yt-uniq batch`` worker
    serialises cleanly instead of returning SQLITE_BUSY.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.executescript(_SCHEMA)
    return conn


def record_run(
    *,
    source_width: int,
    source_height: int,
    source_duration_sec: float,
    codec: str,
    encoder_kind: str,
    workers: int,
    segment_sec: float,
    wall_clock_sec: float,
    pgo_path: Path = DEFAULT_PGO_PATH,
) -> None:
    """Persist a successful run's operating point.

    Silently noops when the source duration is too short to extrapolate
    from (under 5 s) or when wall_clock_sec is non-positive — bad data
    would skew future predictions.
    """
    if source_duration_sec < 5.0 or wall_clock_sec <= 0.0:
        return
    bucket = _bucket_resolution(source_width, source_height)
    seconds_per_min = wall_clock_sec / (source_duration_sec / 60.0)
    import time
    try:
        conn = _connect(pgo_path)
    except sqlite3.Error as exc:
        _log.warning("PGO cache open failed (%s); skipping record", exc)
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO pgo_runs "
            "(resolution_bucket, codec, encoder_kind, workers, segment_sec, "
            "seconds_per_min, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bucket, codec, encoder_kind, workers, segment_sec,
             seconds_per_min, time.time()),
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        _log.warning("PGO cache write failed (%s); skipping", exc)
    finally:
        conn.close()


def predict(
    *,
    source_width: int,
    source_height: int,
    codec: str,
    encoder_kind: str,
    pgo_path: Path = DEFAULT_PGO_PATH,
) -> PgoPrediction | None:
    """Return the best-known operating point for the given key, or None.

    We use the most recent record as the prediction rather than an
    average — encoders, drivers, and ffmpeg builds change underfoot,
    and a recent run is a better predictor than an aggregate that
    weighs ancient hardware.
    """
    bucket = _bucket_resolution(source_width, source_height)
    if not pgo_path.exists():
        return None
    try:
        conn = _connect(pgo_path)
    except sqlite3.Error as exc:
        _log.warning("PGO cache open failed (%s); falling back to heuristic", exc)
        return None
    try:
        row = conn.execute(
            "SELECT workers, segment_sec, seconds_per_min "
            "FROM pgo_runs "
            "WHERE resolution_bucket = ? AND codec = ? AND encoder_kind = ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (bucket, codec, encoder_kind),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("PGO cache read failed (%s); falling back to heuristic", exc)
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return PgoPrediction(
        workers=int(row[0]),
        segment_sec=float(row[1]),
        seconds_per_min_of_video=float(row[2]),
    )


def purge(pgo_path: Path = DEFAULT_PGO_PATH) -> None:
    """Drop the cache file.  Cheap recovery from stale hardware data."""
    if pgo_path.exists():
        pgo_path.unlink()
