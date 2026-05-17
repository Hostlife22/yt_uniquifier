"""Unit tests for Corpus.search_match against mocked target fingerprints."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from yt_uniquifier.core.qa import corpus as corpus_mod
from yt_uniquifier.core.qa.corpus import Corpus, CorpusEntry


def _solid(color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (256, 256), color)


def _seed_entry(corpus: Corpus, path: Path, phashes: tuple[int, ...],
                audio: tuple[int, ...] = ()) -> CorpusEntry:
    e = CorpusEntry(
        id=corpus_mod._hash_path(path), path=path.absolute(), added_at=time.time(),
        duration_sec=10.0, phash_frames=phashes, audio_fingerprint=audio,
        sample_count=len(phashes),
    )
    corpus._upsert(e)
    return e


def test_search_finds_self(tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "x.mp4"
    target.touch()
    # Both target and stored entry have the same fake phashes.
    monkeypatch.setattr(corpus_mod.phash, "sample_frames",
                        lambda _p, n=60: [_solid((0, 0, 0)) for _ in range(5)])
    monkeypatch.setattr(corpus_mod.audio_fp, "fpcalc_available", lambda: False)
    # imagehash of all-black is some specific int; just snapshot it.
    import imagehash
    expected = int(str(imagehash.phash(_solid((0, 0, 0)))), 16)
    c = Corpus(tmp_path / "corpus")
    _seed_entry(c, target, (expected,) * 5)

    matches = c.search_match(target, threshold=0.5)
    assert len(matches) == 1
    assert matches[0].combined > 0.99


def test_search_misses_different_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.mp4"
    target.touch()
    monkeypatch.setattr(corpus_mod.phash, "sample_frames",
                        lambda _p, n=60: [_solid((0, 0, 0)) for _ in range(5)])
    monkeypatch.setattr(corpus_mod.audio_fp, "fpcalc_available", lambda: False)
    import imagehash
    target_hash = int(str(imagehash.phash(_solid((0, 0, 0)))), 16)
    c = Corpus(tmp_path / "corpus")
    # Inverted bits → Hamming distance 64 → visual similarity 0.0, below 0.5.
    _seed_entry(c, tmp_path / "other.mp4",
                (target_hash ^ 0xFFFFFFFFFFFFFFFF,) * 5)
    matches = c.search_match(target, threshold=0.5)
    assert matches == []


def test_search_sorts_by_combined_descending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "t.mp4"
    target.touch()
    import imagehash
    target_hash = int(str(imagehash.phash(_solid((0, 0, 0)))), 16)

    monkeypatch.setattr(corpus_mod.phash, "sample_frames",
                        lambda _p, n=60: [_solid((0, 0, 0)) for _ in range(5)])
    monkeypatch.setattr(corpus_mod.audio_fp, "fpcalc_available", lambda: False)

    c = Corpus(tmp_path / "corpus")
    # Lower-similarity (one bit differs).
    _seed_entry(c, tmp_path / "loose.mp4", (target_hash ^ 0xF,) * 5)
    # Higher-similarity (exact match).
    _seed_entry(c, tmp_path / "exact.mp4", (target_hash,) * 5)

    matches = c.search_match(target, threshold=0.0)
    assert len(matches) == 2
    assert matches[0].combined >= matches[1].combined


def test_threshold_filters(tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "t.mp4"
    target.touch()
    monkeypatch.setattr(corpus_mod.phash, "sample_frames",
                        lambda _p, n=60: [_solid((0, 0, 0)) for _ in range(3)])
    monkeypatch.setattr(corpus_mod.audio_fp, "fpcalc_available", lambda: False)

    c = Corpus(tmp_path / "corpus")
    # Garbage hash → similarity will be very low.
    _seed_entry(c, tmp_path / "garbage.mp4", (0xFFFFFFFFFFFFFFFF,) * 3)
    assert c.search_match(target, threshold=0.99) == []
