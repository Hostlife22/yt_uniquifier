"""v1.2.0 Task 28 — PGO cache unit tests.

Covers:

  * resolution bucketing (1920x1080 → "1080p"; 320x180 → "low")
  * record + predict round-trip
  * predict returns the most recent record (not an average)
  * eta_seconds linear projection
  * predict returns None when the cache file is missing
  * predict returns None when no matching key exists
  * record silently noops on too-short source / zero wall clock
  * concurrent writers serialise without corruption
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from yt_uniquifier.core import pgo


def test_bucket_1080p() -> None:
    assert pgo._bucket_resolution(1920, 1080) == "1080p"
    # Near-1080p (slightly cropped) buckets the same way.
    assert pgo._bucket_resolution(1916, 1078) == "1080p"


def test_bucket_4k() -> None:
    assert pgo._bucket_resolution(3840, 2160) == "4k"


def test_bucket_low() -> None:
    assert pgo._bucket_resolution(320, 180) == "low"


def test_record_then_predict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=300.0,
        codec="h264", encoder_kind="x264",
        workers=4, segment_sec=10.0, wall_clock_sec=120.0,
        pgo_path=path,
    )
    prediction = pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="x264",
        pgo_path=path,
    )
    assert prediction is not None
    assert prediction.workers == 4
    assert prediction.segment_sec == 10.0
    # 120 s wall / (300 s / 60) = 24 s per minute of video.
    assert prediction.seconds_per_min_of_video == pytest.approx(24.0)


def test_predict_uses_most_recent_record(tmp_path: Path) -> None:
    """An older slow run must not override a recent fast run."""
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=60.0,
        codec="h264", encoder_kind="x264",
        workers=2, segment_sec=10.0, wall_clock_sec=60.0,
        pgo_path=path,
    )
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=60.0,
        codec="h264", encoder_kind="x264",
        workers=8, segment_sec=5.0, wall_clock_sec=15.0,
        pgo_path=path,
    )
    prediction = pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="x264",
        pgo_path=path,
    )
    assert prediction is not None
    assert prediction.workers == 8
    assert prediction.segment_sec == 5.0


def test_predict_returns_none_when_cache_missing(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.sqlite"
    assert not path.exists()
    assert pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="x264",
        pgo_path=path,
    ) is None


def test_predict_returns_none_for_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=60.0,
        codec="h264", encoder_kind="x264",
        workers=4, segment_sec=10.0, wall_clock_sec=30.0,
        pgo_path=path,
    )
    # Same resolution + codec, different encoder.  Cache miss → None.
    assert pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="nvenc",
        pgo_path=path,
    ) is None


def test_eta_seconds_extrapolates_linearly() -> None:
    p = pgo.PgoPrediction(workers=4, segment_sec=10.0, seconds_per_min_of_video=24.0)
    # 5 min source → 5 × 24 = 120 s ETA.
    assert p.eta_seconds(300.0) == pytest.approx(120.0)
    # 90 s source → 1.5 × 24 = 36 s.
    assert p.eta_seconds(90.0) == pytest.approx(36.0)


def test_record_noops_on_too_short_source(tmp_path: Path) -> None:
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=1.0,
        codec="h264", encoder_kind="x264",
        workers=4, segment_sec=10.0, wall_clock_sec=30.0,
        pgo_path=path,
    )
    # File may or may not have been created (schema runs at connect),
    # but predict must return None because no row was inserted.
    assert pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="x264",
        pgo_path=path,
    ) is None


def test_record_noops_on_zero_wall_clock(tmp_path: Path) -> None:
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=60.0,
        codec="h264", encoder_kind="x264",
        workers=4, segment_sec=10.0, wall_clock_sec=0.0,
        pgo_path=path,
    )
    assert pgo.predict(
        source_width=1920, source_height=1080,
        codec="h264", encoder_kind="x264",
        pgo_path=path,
    ) is None


def test_concurrent_writers_serialise_cleanly(tmp_path: Path) -> None:
    """Two writers racing on the same key must both land without
    SQLITE_BUSY surfacing to the caller.  ``BEGIN IMMEDIATE`` + the
    10 s timeout in _connect handle the contention."""
    path = tmp_path / "pgo.sqlite"

    def writer(workers: int) -> None:
        pgo.record_run(
            source_width=1920, source_height=1080, source_duration_sec=60.0,
            codec="h264", encoder_kind="x264",
            workers=workers, segment_sec=10.0, wall_clock_sec=30.0,
            pgo_path=path,
        )

    threads = [threading.Thread(target=writer, args=(i,)) for i in (1, 2, 3, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Final state: 4 rows landed (one per writer); predict returns one.
    conn = sqlite3.connect(str(path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM pgo_runs").fetchone()[0]
    finally:
        conn.close()
    assert count == 4


def test_purge_removes_cache_file(tmp_path: Path) -> None:
    path = tmp_path / "pgo.sqlite"
    pgo.record_run(
        source_width=1920, source_height=1080, source_duration_sec=60.0,
        codec="h264", encoder_kind="x264",
        workers=4, segment_sec=10.0, wall_clock_sec=30.0,
        pgo_path=path,
    )
    assert path.exists()
    pgo.purge(path)
    assert not path.exists()
    # Purge is idempotent.
    pgo.purge(path)
