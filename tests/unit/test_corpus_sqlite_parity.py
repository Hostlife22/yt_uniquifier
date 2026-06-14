"""v0.8.0 R2 — SQLite corpus parity, migration, concurrency.

Covers the new ``corpus_db.CorpusDB`` storage and the auto-migration of
a legacy ``index.json`` that ``Corpus(root)`` triggers on first open.

Parity property: every entry that round-trips through the legacy JSON
path comes back bit-identical out of the SQLite store. That's the
contract upgrade-in-place users rely on — if a corpus survives across a
yt-uniquifier version bump, none of its phash/audio fingerprints may
silently drift.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from yt_uniquifier.core.qa.corpus import Corpus, CorpusEntry
from yt_uniquifier.core.qa.corpus_db import (
    LEGACY_JSON_FILENAME,
    SQLITE_FILENAME,
    CorpusDB,
    _pack_ints,
    _unpack_ints,
    migrate_from_json,
)


def _legacy_payload(entries: list[dict[str, object]]) -> str:
    return json.dumps({"schema_version": 1, "entries": entries}, indent=2)


def _sample_entry(
    eid: str = "abc12345", *, n_phash: int = 8, with_audio: bool = True
) -> CorpusEntry:
    return CorpusEntry(
        id=eid,
        path=Path(f"/tmp/{eid}.mp4"),
        added_at=1_700_000_000.0,
        duration_sec=12.34,
        phash_frames=tuple(range(n_phash)),
        audio_fingerprint=tuple(range(100, 105)) if with_audio else (),
        sample_count=n_phash,
    )


# ---------------------------------------------------------------------------
# Pack / unpack invariants
# ---------------------------------------------------------------------------


def test_pack_unpack_roundtrip_preserves_uint64_values() -> None:
    """phash hashes can be near 2**64 - the encoding must not lose bits."""
    samples = (
        0,
        1,
        2**32,
        2**63,
        2**64 - 1,  # max uint64
        0xDEADBEEFCAFEBABE,
    )
    blob = _pack_ints(samples)
    assert _unpack_ints(blob) == samples


def test_pack_empty_sequence() -> None:
    assert _pack_ints(()) == b""
    assert _unpack_ints(b"") == ()


def test_unpack_truncated_blob_does_not_crash() -> None:
    """Misaligned bytes should produce a shorter tuple, not raise."""
    aligned = _pack_ints((1, 2, 3))
    out = _unpack_ints(aligned + b"\x00\x00")  # 2 trailing bytes
    assert out == (1, 2, 3)


# ---------------------------------------------------------------------------
# CorpusDB CRUD
# ---------------------------------------------------------------------------


def test_corpus_db_add_lookup_iter_purge_len(tmp_path: Path) -> None:
    db = CorpusDB(tmp_path)
    try:
        assert len(db) == 0
        assert db.lookup_by_id("nope") is None
        assert list(db.iter_entries()) == []

        e1 = _sample_entry("aaa11111", n_phash=4)
        e2 = _sample_entry("bbb22222", n_phash=10, with_audio=False)
        db.add_entry(e1)
        db.add_entry(e2)

        assert len(db) == 2
        assert db.lookup_by_id("aaa11111") == e1
        assert db.lookup_by_id("bbb22222") == e2

        # Stable order by added_at ascending. Both fixtures share the same
        # added_at so SQLite is free to return either ordering — we only
        # assert membership here.
        all_entries = list(db.iter_entries())
        assert {e.id for e in all_entries} == {"aaa11111", "bbb22222"}

        # Replace semantics: re-adding the same id overwrites.
        e1_v2 = CorpusEntry(
            id="aaa11111",
            path=Path("/tmp/aaa11111-renamed.mp4"),
            added_at=1_700_000_999.0,
            duration_sec=99.9,
            phash_frames=(42,),
            audio_fingerprint=(),
            sample_count=1,
        )
        db.add_entry(e1_v2)
        assert len(db) == 2
        assert db.lookup_by_id("aaa11111") == e1_v2

        assert db.purge("aaa11111") is True
        assert db.purge("aaa11111") is False
        assert len(db) == 1
    finally:
        db.close()


def test_corpus_db_context_manager_closes(tmp_path: Path) -> None:
    with CorpusDB(tmp_path) as db:
        db.add_entry(_sample_entry())
        assert len(db) == 1
    # Re-open afterwards must work (file lock released).
    db2 = CorpusDB(tmp_path)
    try:
        assert len(db2) == 1
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# Auto-migration from legacy index.json
# ---------------------------------------------------------------------------


def test_open_corpus_auto_migrates_legacy_json(tmp_path: Path) -> None:
    """First Corpus() open should fold an existing index.json into SQLite."""
    legacy = [
        {
            "id": "legacy1",
            "path": "/tmp/one.mp4",
            "added_at": 1_700_000_001.0,
            "duration_sec": 5.5,
            "phash_frames": [11, 22, 33],
            "audio_fingerprint": [],
            "sample_count": 3,
        },
        {
            "id": "legacy2",
            "path": "/tmp/two.mp4",
            "added_at": 1_700_000_002.0,
            "duration_sec": 6.6,
            "phash_frames": [44],
            "audio_fingerprint": [101, 102],
            "sample_count": 1,
        },
    ]
    (tmp_path / LEGACY_JSON_FILENAME).write_text(_legacy_payload(legacy))

    c = Corpus(tmp_path)
    entries = c.list_all()
    assert len(entries) == 2
    assert {e.id for e in entries} == {"legacy1", "legacy2"}

    # SQLite store now exists.
    assert (tmp_path / SQLITE_FILENAME).exists()
    # Legacy JSON has been renamed (kept as recovery hatch).
    assert not (tmp_path / LEGACY_JSON_FILENAME).exists()
    backups = list(tmp_path.glob(f"{LEGACY_JSON_FILENAME}.migrated.*"))
    assert len(backups) == 1, "expected exactly one .migrated.<ts> backup"

    # Round-trip via SQLite preserves the int sequences exactly.
    legacy1 = next(e for e in entries if e.id == "legacy1")
    assert legacy1.phash_frames == (11, 22, 33)
    legacy2 = next(e for e in entries if e.id == "legacy2")
    assert legacy2.audio_fingerprint == (101, 102)


def test_migration_is_one_shot(tmp_path: Path) -> None:
    """A second Corpus() open with the migrated backup present must not
    re-migrate or duplicate rows."""
    (tmp_path / LEGACY_JSON_FILENAME).write_text(_legacy_payload([
        {"id": "x", "path": "/tmp/x.mp4", "added_at": 1.0, "duration_sec": 1.0,
         "phash_frames": [1], "audio_fingerprint": [], "sample_count": 1},
    ]))
    Corpus(tmp_path).list_all()  # first open: migrates
    # User manually restores a stale JSON (e.g. backup tool). It must NOT
    # be re-folded into the SQLite store because that store is already
    # populated — silently merging would let stale fingerprints reappear.
    (tmp_path / LEGACY_JSON_FILENAME).write_text(_legacy_payload([
        {"id": "y", "path": "/tmp/y.mp4", "added_at": 2.0, "duration_sec": 2.0,
         "phash_frames": [2], "audio_fingerprint": [], "sample_count": 1},
    ]))
    entries = Corpus(tmp_path).list_all()
    assert {e.id for e in entries} == {"x"}, "second open must NOT auto-merge"


def test_corrupt_legacy_json_does_not_break_open(tmp_path: Path) -> None:
    (tmp_path / LEGACY_JSON_FILENAME).write_text("{ broken ::: not json")
    # Must not raise; corpus starts empty and the broken JSON is left
    # in place so a user can hand-recover it.
    c = Corpus(tmp_path)
    assert c.list_all() == []
    assert (tmp_path / LEGACY_JSON_FILENAME).exists(), (
        "corrupt JSON must NOT be renamed/destroyed"
    )


def test_migrate_from_json_skips_malformed_entries(tmp_path: Path) -> None:
    legacy = [
        {"id": "good", "path": "/tmp/good.mp4", "added_at": 1.0,
         "duration_sec": 1.0, "phash_frames": [1], "audio_fingerprint": [], "sample_count": 1},
        {"id": "bad-no-path", "added_at": 1.0},   # missing required path
        {"id": "bad-types", "path": "/tmp/x.mp4", "phash_frames": "not-a-list"},
    ]
    json_path = tmp_path / LEGACY_JSON_FILENAME
    json_path.write_text(_legacy_payload(legacy))

    db = CorpusDB(tmp_path)
    try:
        # Auto-migration already ran in __init__, but it's the same code
        # path; explicit assertion is on the resulting state.
        assert len(db) == 1
        good = db.lookup_by_id("good")
        assert good is not None
        assert good.phash_frames == (1,)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_writers_do_not_lose_rows(tmp_path: Path) -> None:
    """Many threads all calling add_entry must each land a row.

    The old JSON path lost writes under thread contention (read snapshot,
    mutate, write — last writer wins). SQLite serialises via BEGIN
    IMMEDIATE; this regression-guards the upgrade.
    """
    db = CorpusDB(tmp_path)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(start: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            for i in range(20):
                eid = f"thr{start:02d}_{i:02d}"
                db.add_entry(_sample_entry(eid, n_phash=2, with_audio=False))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert errors == [], errors
    assert len(db) == 8 * 20
    db.close()


def test_iter_entries_during_writer(tmp_path: Path) -> None:
    """A reader concurrent with a writer must not deadlock or crash."""
    db = CorpusDB(tmp_path)
    for i in range(50):
        db.add_entry(_sample_entry(f"seed{i:03d}", n_phash=2, with_audio=False))

    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                _ = list(db.iter_entries())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(50):
                db.add_entry(_sample_entry(f"new{i:03d}", n_phash=2, with_audio=False))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    r = threading.Thread(target=reader, daemon=True)
    w = threading.Thread(target=writer)
    r.start()
    w.start()
    w.join(timeout=10.0)
    stop.set()
    r.join(timeout=2.0)

    assert errors == [], errors
    assert len(db) == 100
    db.close()


# ---------------------------------------------------------------------------
# explicit migrate_from_json + return value
# ---------------------------------------------------------------------------


def test_migrate_from_json_returns_inserted_count_and_renames(tmp_path: Path) -> None:
    legacy = [
        {"id": f"e{i}", "path": f"/tmp/{i}.mp4", "added_at": float(i),
         "duration_sec": float(i), "phash_frames": [i], "audio_fingerprint": [],
         "sample_count": 1}
        for i in range(5)
    ]
    json_path = tmp_path / LEGACY_JSON_FILENAME
    json_path.write_text(_legacy_payload(legacy))

    # Construct DB in an isolated subdir so its auto-migration does NOT
    # consume the target file before we call migrate_from_json explicitly.
    db_dir = tmp_path / "db"
    db = CorpusDB(db_dir)
    try:
        inserted = migrate_from_json(json_path, db)
        assert inserted == 5
        assert len(db) == 5
        # Source renamed to .migrated.<ts>
        assert not json_path.exists()
        assert list(tmp_path.glob(f"{LEGACY_JSON_FILENAME}.migrated.*"))
    finally:
        db.close()
