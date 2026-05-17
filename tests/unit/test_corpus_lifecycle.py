"""Unit tests for Corpus.add/list/remove + atomic JSON storage."""

from __future__ import annotations

import json
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


def test_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    c = Corpus(tmp_path / "corpus")
    entry = c.add(src)
    assert c.remove(entry.id) is True
    assert c.list_all() == []
    assert c.remove(entry.id) is False  # idempotent


def test_atomic_index_write(tmp_path: Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_phash_and_audio(monkeypatch)
    src = tmp_path / "movie.mp4"
    src.touch()
    c = Corpus(tmp_path / "corpus")
    c.add(src)
    # No leftover tmp file.
    assert not (tmp_path / "corpus" / "index.json.tmp").exists()
    raw = json.loads((tmp_path / "corpus" / "index.json").read_text())
    assert raw["schema_version"] == 1
    assert len(raw["entries"]) == 1


def test_corrupt_index_returns_empty(tmp_path: Path) -> None:
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
