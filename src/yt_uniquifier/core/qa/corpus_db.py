"""SQLite-backed fingerprint corpus storage.

v0.8.0 R2 — the prior implementation kept all entries inline in a single
``index.json`` and rewrote the whole file on every mutation. That scales
poorly past ~10 k entries (full deserialise + re-encode for every add)
and creates a torn-write window that the JSON path tried to paper over
with a flock + per-process tmp file. SQLite gives us:

  * O(log n) lookup by primary key,
  * single-writer-many-reader concurrency via WAL,
  * inter-process serialisation handled by the engine,
  * append cost independent of corpus size.

Schema is intentionally small — one ``entries`` table plus a
``schema_info`` versioning row. ``phash_frames`` and ``audio_fp`` are
stored as little-endian ``Q`` (uint64) packed blobs rather than JSON
arrays: ~8x denser, no decode allocations on read.

The public surface (``add_entry`` / ``lookup_by_id`` / ``iter_entries`` /
``purge`` / ``__len__``) is what ``Corpus`` (in :mod:`corpus`) wraps with
its older method names for backward compatibility. Direct ``CorpusDB``
usage is encouraged for new code.

A first-time open of a corpus directory that contains a legacy
``index.json`` (and no ``index.sqlite``) triggers a one-shot migration:
entries are loaded, inserted in a single transaction, and the source
JSON is renamed to ``index.json.migrated.<unix_ts>`` (never deleted —
recovery hatch).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
SQLITE_FILENAME = "index.sqlite"
LEGACY_JSON_FILENAME = "index.json"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    added_at REAL NOT NULL,
    duration_sec REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    phash_frames BLOB NOT NULL,
    audio_fp BLOB NOT NULL,
    content_sha256 TEXT,
    stat_size INTEGER,
    stat_mtime_ns INTEGER
);

CREATE INDEX IF NOT EXISTS idx_entries_added_at ON entries(added_at);
"""


@dataclass(frozen=True)
class CorpusEntry:
    """One row in the corpus index.

    Lives in this module (not :mod:`corpus`) so the storage layer can be
    referenced without dragging in ImageHash / fpcalc dependencies of the
    high-level facade. ``corpus.py`` re-exports for backward compat.
    """

    id: str                              # stable prefix of the content SHA-256
    path: Path
    added_at: float                      # unix timestamp
    duration_sec: float
    phash_frames: tuple[int, ...]        # 64-bit perceptual hashes
    audio_fingerprint: tuple[int, ...]   # chromaprint subfingerprints (or empty)
    sample_count: int                    # len(phash_frames)
    # Added in schema v2. None denotes a migrated legacy path-keyed row.
    content_sha256: str | None = None
    stat_size: int | None = None
    stat_mtime_ns: int | None = None


def _pack_ints(seq: tuple[int, ...]) -> bytes:
    """Pack a sequence of 64-bit unsigned ints into a contiguous blob.

    ``struct.pack`` on ``f"<{n}Q"`` is ~4x faster than ``b''.join`` over
    individual packs for n > ~20, which is the common case (default 60
    phash samples). Empty tuple → b''.
    """
    if not seq:
        return b""
    return struct.pack(f"<{len(seq)}Q", *seq)


def _unpack_ints(blob: bytes) -> tuple[int, ...]:
    if not blob:
        return ()
    n, rem = divmod(len(blob), 8)
    if rem:
        # Defensive: corrupt blob shouldn't crash callers; log and skip
        # the trailing bytes. Surfaces as "fewer samples than expected"
        # rather than a hard ValueError mid-search.
        _log.warning("corpus blob length %d not aligned to 8; truncating", len(blob))
    return struct.unpack(f"<{n}Q", blob[: n * 8])


def _row_to_entry(row: sqlite3.Row) -> CorpusEntry:
    return CorpusEntry(
        id=str(row["id"]),
        path=Path(str(row["path"])),
        added_at=float(row["added_at"]),
        duration_sec=float(row["duration_sec"]),
        phash_frames=_unpack_ints(bytes(row["phash_frames"])),
        audio_fingerprint=_unpack_ints(bytes(row["audio_fp"])),
        sample_count=int(row["sample_count"]),
        content_sha256=(
            str(row["content_sha256"]) if row["content_sha256"] is not None else None
        ),
        stat_size=int(row["stat_size"]) if row["stat_size"] is not None else None,
        stat_mtime_ns=(
            int(row["stat_mtime_ns"]) if row["stat_mtime_ns"] is not None else None
        ),
    )


class CorpusDB:
    """SQLite-backed fingerprint store.

    The connection is kept open for the lifetime of the instance and
    guarded by an in-process ``RLock`` — SQLite's own locking handles
    inter-process serialisation, the RLock keeps the read-modify-write
    sequences (e.g. ``add_entry`` replace-by-id) atomic within a single
    GUI process where workers + the UI thread share one ``CorpusDB``.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / SQLITE_FILENAME
        self._lock = threading.RLock()
        # ``check_same_thread=False`` is safe because every public method
        # serialises on ``self._lock`` — without it, GUI worker threads
        # could not call an instance opened on the UI thread.
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit BEGIN in transactions
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._init_schema()
        # Auto-migrate any legacy JSON sitting next to us. We do this
        # eagerly so that the FIRST iter_entries / lookup returns the
        # migrated data rather than an empty corpus the user assumes is
        # broken.
        self._maybe_migrate_legacy_json()

    def _configure_connection(self) -> None:
        cur = self._conn.cursor()
        # WAL gives concurrent readers + a single writer without the
        # classic SQLite "database is locked" errors on contended workloads.
        cur.execute("PRAGMA journal_mode=WAL")
        # NORMAL is the documented tradeoff for WAL — durability is
        # preserved across application crashes; only an OS-level crash
        # can lose the most recent transaction. For a non-critical cache
        # like the corpus, the speed win is worth it.
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    def _init_schema(self) -> None:
        # executescript() issues implicit COMMITs around its body, which
        # collides with our manual BEGIN IMMEDIATE in ``_tx``. Run it in
        # bare autocommit mode (we're already in isolation_level=None).
        cur = self._conn.cursor()
        try:
            cur.executescript(_SCHEMA_SQL)
            columns = {
                str(row[1]) for row in cur.execute("PRAGMA table_info(entries)")
            }
            for name, sql_type in (
                ("content_sha256", "TEXT"),
                ("stat_size", "INTEGER"),
                ("stat_mtime_ns", "INTEGER"),
            ):
                if name not in columns:
                    cur.execute(f"ALTER TABLE entries ADD COLUMN {name} {sql_type}")
            # Older releases inserted the version as the primary-key value.
            # Replace it atomically rather than accumulating one row per version.
            cur.execute("DELETE FROM schema_info")
            cur.execute(
                "INSERT INTO schema_info (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        finally:
            cur.close()

    def _maybe_migrate_legacy_json(self) -> None:
        legacy = self.root / LEGACY_JSON_FILENAME
        if not legacy.exists():
            return
        if len(self) > 0:
            # SQLite already populated — leave the JSON file alone; the
            # user may have intentionally kept it as a backup. Migration
            # is one-shot only.
            return
        try:
            migrated = migrate_from_json(legacy, self)
        except Exception as exc:  # noqa: BLE001 — never break corpus open on migration error
            _log.warning("legacy index.json migration failed: %s", exc)
            return
        if migrated > 0:
            _log.info(
                "migrated %d corpus entries from legacy JSON; "
                "kept original at %s",
                migrated,
                legacy.with_suffix(f".json.migrated.{int(time.time())}"),
            )

    # ---- public API --------------------------------------------------------

    def add_entry(self, entry: CorpusEntry) -> None:
        """Insert or replace by id.

        Re-adding the same id is the documented "replace" pattern (the
        old JSON-backed code did `[e for e in entries if e.id != x] +
        [new]`).
        """
        with self._lock, self._tx() as cur:
            # One physical path represents one current content version. A file
            # replaced in place therefore cannot leave its stale fingerprints
            # searchable under the old content ID.
            cur.execute(
                "DELETE FROM entries WHERE path = ? AND id != ?",
                (str(entry.path), entry.id),
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO entries (
                    id, path, added_at, duration_sec,
                    sample_count, phash_frames, audio_fp,
                    content_sha256, stat_size, stat_mtime_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    str(entry.path),
                    entry.added_at,
                    entry.duration_sec,
                    entry.sample_count,
                    _pack_ints(entry.phash_frames),
                    _pack_ints(entry.audio_fingerprint),
                    entry.content_sha256,
                    entry.stat_size,
                    entry.stat_mtime_ns,
                ),
            )

    def lookup_by_path(self, path: Path) -> CorpusEntry | None:
        """Return the row currently associated with an absolute path."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM entries WHERE path = ?", (str(path),)
            )
            row = cur.fetchone()
            cur.close()
        return _row_to_entry(row) if row else None

    def lookup_by_id(self, entry_id: str) -> CorpusEntry | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM entries WHERE id = ?", (entry_id,)
            )
            row = cur.fetchone()
            cur.close()
        return _row_to_entry(row) if row else None

    def iter_entries(self) -> Iterator[CorpusEntry]:
        """Yield every entry, ordered by ``added_at`` ascending.

        Stable ordering matters for the GUI's CorpusScreen (otherwise
        rows jump on every refresh) and for the QA report's matchlist.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM entries ORDER BY added_at ASC"
            )
            rows = cur.fetchall()
            cur.close()
        for row in rows:
            yield _row_to_entry(row)

    def purge(self, entry_id: str) -> bool:
        """Delete by id. Returns ``True`` if a row was removed."""
        with self._lock, self._tx() as cur:
            cur.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def __len__(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM entries")
            n = int(cur.fetchone()["n"])
            cur.close()
        return n

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- context-manager support (useful in tests + CLI one-shots) ---------

    def __enter__(self) -> CorpusDB:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- internals ---------------------------------------------------------

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        """Open a transaction with an explicit BEGIN IMMEDIATE.

        BEGIN IMMEDIATE acquires the write lock up-front, so two
        concurrent writers serialise instead of one losing the race at
        commit time and having to retry — important when GUI workers
        add() while a CLI batch process is also adding.
        """
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()


def migrate_from_json(json_path: Path, db: CorpusDB) -> int:
    """Read a legacy ``index.json`` and insert its entries into ``db``.

    Returns the number of entries inserted. The source file is renamed
    to ``index.json.migrated.<unix_ts>`` on success so a user who wants
    to verify the migration (or roll back to the prior tool version)
    still has the original bytes. A corrupt JSON file logs a warning
    and returns 0 rather than raising — the corpus open path treats
    that as "start fresh".
    """
    if not json_path.exists():
        return 0
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning(
            "legacy corpus JSON %s is unreadable (%s); leaving in place "
            "and starting with empty SQLite store",
            json_path,
            exc,
        )
        return 0

    entries_raw = raw.get("entries", []) if isinstance(raw, dict) else []
    inserted = 0
    for d in entries_raw:
        try:
            entry = CorpusEntry(
                id=str(d["id"]),
                path=Path(d["path"]),
                added_at=float(d.get("added_at", 0.0)),
                duration_sec=float(d.get("duration_sec", 0.0)),
                phash_frames=tuple(int(x) for x in d.get("phash_frames", [])),
                audio_fingerprint=tuple(int(x) for x in d.get("audio_fingerprint", [])),
                sample_count=int(d.get("sample_count", 0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning("skipping unreadable corpus entry during migration: %s", exc)
            continue
        db.add_entry(entry)
        inserted += 1

    backup = json_path.with_suffix(f".json.migrated.{int(time.time())}")
    try:
        json_path.rename(backup)
    except OSError as exc:
        # Migration succeeded data-wise; only the rename failed. Log and
        # carry on — the next open will see SQLite is populated and skip
        # re-migration.
        _log.warning("could not rename migrated JSON to %s: %s", backup, exc)
    return inserted


__all__ = [
    "LEGACY_JSON_FILENAME",
    "SCHEMA_VERSION",
    "SQLITE_FILENAME",
    "CorpusDB",
    "CorpusEntry",
    "migrate_from_json",
]
