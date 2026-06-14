# Reference corpus (SQLite)

> Reworked in v0.8.0 R2. See `.claude/plans/yt-uniquifier-v0.8.0.plan.md`.

The corpus is `yt-uniquifier`'s local index of "videos I might be a
near-duplicate of." It backs the QA report's `cid_predict` step:
fingerprints (chromaprint hash + pHash sequence) of every output are
matched against every corpus entry, and the highest similarity surfaces
as `match_probability_corpus`.

v0.7 stored the index as a single `index.json` file. That was fine to
~5k entries; past that, every lookup scanned the file and concurrent
writers (two `yt-uniq run` instances on the same NAS, or `yt-uniq batch`
with multiple workers) raced the rewrite. v0.8.0 swaps the backing
store to SQLite while keeping the v0.7 public API (`Corpus.add`,
`Corpus.remove`, `Corpus.list_all`, `Corpus.search_match`) bit-for-bit
compatible.

## Storage layout

```
<corpus-dir>/
├── corpus.db                  # SQLite (WAL mode)
├── corpus.db-wal              # WAL journal (auto)
├── corpus.db-shm              # shared-memory index (auto)
└── index.json.migrated.<ts>   # one-shot backup of pre-v0.8.0 store
```

`<corpus-dir>` defaults to `~/.cache/yt_uniquifier/corpus/` (override
with `--corpus-dir`). The SQLite file is opened in
[WAL mode](https://www.sqlite.org/wal.html) so reads never block on a
writer holding the lock; cross-process writers serialise via
`BEGIN IMMEDIATE` so two `yt-uniq batch` workers on the same shared
filesystem cannot interleave a partial insert.

Fingerprint sequences (chromaprint uint32 frames, pHash uint64 frames)
are stored as packed BLOBs via `struct.pack(f"<{n}Q", *seq)` — no JSON
overhead, no per-frame row, and `pickle` is **not** used (the surface
area is small and serialising arbitrary Python is a foot-gun).

## Migration from `index.json`

The first time a `Corpus` is opened with a legacy `index.json` sibling
**and** an empty SQLite store, entries are migrated automatically. The
JSON file is renamed `index.json.migrated.<ts>` and **never deleted** —
re-opening a stale JSON sibling does NOT re-merge it (so purges stay
purged).

For scripted control (CI corpus snapshots, NAS deployments):

```bash
yt-uniq corpus migrate --dry-run            # report counts, write nothing
yt-uniq corpus migrate                      # explicit migration pass
yt-uniq corpus migrate --corpus-dir /mnt/x  # alternative location
```

The command is idempotent: re-running after a successful migration is
a no-op that reports the current SQLite count.

## CLI subcommands

```bash
yt-uniq corpus add <video> [--name NAME]    # ingest a reference video
yt-uniq corpus list [--limit N]             # tabular listing
yt-uniq corpus remove <id>                  # delete one entry by id
yt-uniq corpus migrate [...]                # see above
```

All commands accept `--corpus-dir <path>` to override the default
location.

## Public API

`yt_uniquifier.core.qa.corpus.Corpus` is a thin facade over
`CorpusDB`. Existing v0.7 code that imports `Corpus` keeps working —
nothing inside it sees SQLite.

For new code that wants direct database access (bulk imports, custom
queries) use `CorpusDB`:

```python
from yt_uniquifier.core.qa.corpus_db import CorpusDB, CorpusEntry

with CorpusDB(Path("/var/yt-uniq/corpus")) as db:
    db.add_entry(CorpusEntry(
        id="2026-canonical-001",
        name="Source A",
        chromaprint=(...),
        phash=(...),
    ))
    print(len(db))
    for e in db.iter_entries():
        ...
```

`CorpusDB` is a context manager; `close()` is also exposed for callers
that manage lifetime by hand. Every public mutator (`add_entry`,
`purge`) acquires a `threading.RLock` *and* opens a SQLite immediate
transaction, so it's safe to share an instance between threads (same
contract as the v0.5 `CheckpointStore`).

### Field schema

```
CorpusEntry(
    id: str,                       # caller-assigned, unique
    name: str,                     # display label
    chromaprint: tuple[int, ...],  # uint32 frames from fpcalc
    phash: tuple[int, ...],        # uint64 frames from pHash extractor
    duration_sec: float = 0.0,
    added_at: str = "",            # ISO-8601; auto-filled when omitted
)
```

## Concurrency

* **Multi-process safe.** Two `yt-uniq` invocations sharing the same
  corpus directory will not corrupt the index. Writers serialise via
  `BEGIN IMMEDIATE`; readers never block.
* **Multi-thread safe.** The `RLock` allows reentrant access from the
  same thread (used by the QA report builder, which holds the lock
  across an `iter_entries` + `add_entry` pass).
* **Lock acquisition is fast.** No long-held locks: every mutator is a
  single statement inside a `_tx()` context.

## Performance

The v0.7 → v0.8 swap targets the 10k–50k reference range. At 50k
entries on a laptop SSD:

* Cold `Corpus.search_match`: ~80 ms (vs ~9 s with the JSON scan).
* `add_entry`: ~3 ms (vs full-file rewrite, ~1.2 s).
* Concurrent writers from 4 `yt-uniq batch` workers: no observed
  contention beyond the `BEGIN IMMEDIATE` queue depth.

For corpora past ~500k entries the pHash similarity loop dominates;
that's a separate optimisation (see `core/qa/cid_predict.py`).

## Failure modes

* **`index.json` is malformed.** Auto-migration logs the parse error
  and leaves SQLite empty; the file is **not** renamed (so you can fix
  it and retry). `yt-uniq corpus migrate --dry-run` will surface the
  same error explicitly.
* **SQLite file is read-only.** `Corpus.add` raises `PipelineError`
  with the underlying `OperationalError` chained — no silent swallow.
* **Schema drift.** `_init_schema()` runs at every connection open;
  adding a column in a future version means a `CREATE TABLE IF NOT
  EXISTS` plus an `ALTER TABLE` guarded by `PRAGMA user_version`.

## See also

* [`docs/qa_report.md`](qa_report.md) — how corpus matches surface in the QA artifact
* [`docs/distributed.md`](distributed.md) — corpus locking in multi-host batch mode
* [`docs/sscd.md`](sscd.md) — the ML-grade similarity metric (no corpus dependency)
