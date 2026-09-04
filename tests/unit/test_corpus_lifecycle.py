"""Unit tests for Corpus.add/list/remove + atomic JSON storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.qa import corpus as corpus_mod
from yt_uniquifier.core.qa.corpus import Corpus, CorpusEntry


def _patch_phash_and_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(corpus_mod.phash, "_probe_duration", lambda _p: 10.0)
    monkeypatch.setattr(corpus_mod.phash, "sample_frames", lambda _p, n=60: [])
    monkeypatch.setattr(corpus_mod.audio_fp, "fpcalc_available", lambda: False)


def test_empty_corpus_lists_nothing(tmp_path: Path) -> None:
    c = Corpus(tmp_path)
    assert c.list_all() == []


def test_add_lists_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    c = Corpus(tmp_path / "corpus")
    entry = c.add(src)
    assert entry.path == src.absolute()
    assert entry.sample_count == 0  # no frames in our mock
    assert len(c.list_all()) == 1


def test_add_same_path_replaces_entry(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    c = Corpus(tmp_path / "corpus")
    c.add(src)
    c.add(src)
    assert len(c.list_all()) == 1


def test_move_preserves_content_id(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    original = tmp_path / "original.mp4"
    moved = tmp_path / "moved.mp4"
    original.write_bytes(b"licensed-content")
    corpus = Corpus(tmp_path / "corpus")
    first = corpus.add(original)
    original.rename(moved)
    second = corpus.add(moved)

    assert second.id == first.id
    assert second.path == moved.resolve()
    assert len(corpus.list_all()) == 1


def test_replaced_content_invalidates_old_id(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"version-one")
    corpus = Corpus(tmp_path / "corpus")
    first = corpus.add(source)
    source.write_bytes(b"version-two-with-different-bytes")
    second = corpus.add(source)

    assert second.id != first.id
    assert corpus._db.lookup_by_id(first.id) is None
    assert [entry.id for entry in corpus.list_all()] == [second.id]


def test_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    c = Corpus(tmp_path / "corpus")
    entry = c.add(src)
    assert c.remove(entry.id) is True
    assert c.list_all() == []
    assert c.remove(entry.id) is False  # idempotent


def test_sqlite_store_is_created_and_populated(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.8.0 R2 — storage moved from index.json to index.sqlite.

    The lifecycle test no longer asserts on the JSON file layout; that
    concern moved to ``test_corpus_sqlite_parity.py``. What we still
    care about here: after add(), the persisted store exists, contains
    the entry, and the connection released its WAL+SHM cleanly so a
    second Corpus() open sees the same data.
    """
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    corpus_dir = tmp_path / "corpus"
    c = Corpus(corpus_dir)
    c.add(src)
    assert (corpus_dir / "index.sqlite").exists()
    # Re-opening from a different Corpus handle must see the row — proves
    # the write was committed, not just kept in the connection's WAL.
    assert len(Corpus(corpus_dir).list_all()) == 1


def test_corrupt_legacy_json_returns_empty(tmp_path: Path) -> None:
    """A pre-v0.8 corpus with a corrupt index.json must not break open().

    The new code attempts to auto-migrate; on parse failure it logs a
    warning, leaves the JSON in place, and starts the SQLite store empty.
    """
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "index.json").write_text("{ broken")
    c = Corpus(corpus_dir)
    assert c.list_all() == []


def test_missing_file_raises(tmp_path: Path) -> None:
    c = Corpus(tmp_path)
    with pytest.raises(FileNotFoundError):
        c.add(tmp_path / "nonexistent.mp4")


def test_entry_dataclass_is_immutable() -> None:
    e = CorpusEntry(
        id="abc", path=Path("/tmp/x.mp4"), added_at=0.0, duration_sec=10,
        phash_frames=(1, 2, 3), audio_fingerprint=(), sample_count=3,
    )
    with pytest.raises(Exception):  # noqa: B017
        e.id = "xyz"  # type: ignore[misc]
