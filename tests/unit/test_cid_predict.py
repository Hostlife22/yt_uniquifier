"""Unit tests for cid_predict.predict.

We mock imagehash.phash and the audio fingerprint helper so the math we're
testing (per-chunk similarity aggregation + weakest_chunk pick) doesn't
depend on real pHash behaviour over arbitrary fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from yt_uniquifier.core.qa import cid_predict


class _FakeHash:
    def __init__(self, value: int) -> None:
        self.value = value & ((1 << 64) - 1)

    def __str__(self) -> str:
        return f"{self.value:016x}"


def _scripted_phash(hashes_in: list[int], hashes_out: list[int]):
    """Return a callable that yields scripted ints — input frames first, then output."""
    sequence: list[int] = list(hashes_in) + list(hashes_out)
    it: Iterator[int] = iter(sequence)

    def _phash(_img: Image.Image) -> _FakeHash:
        return _FakeHash(next(it))

    return _phash


class _ImagehashStub:
    def __init__(self, phash):
        self.phash = phash


def _patch(monkeypatch: pytest.MonkeyPatch, *, duration: float,
           n_chunks: int,
           phashes_in: list[int], phashes_out: list[int],
           in_fp: list[int] | None = None, out_fp: list[int] | None = None) -> None:
    monkeypatch.setattr(cid_predict.phash, "_probe_duration", lambda _p: duration)
    monkeypatch.setattr(
        cid_predict.phash, "_sample_hashes",
        lambda path, n=60: phashes_in if path.name == "in.mp4" else phashes_out,
    )
    monkeypatch.setattr(
        cid_predict.audio_fp, "fpcalc_available",
        lambda: in_fp is not None and out_fp is not None,
    )
    if in_fp is not None and out_fp is not None:
        monkeypatch.setattr(
            cid_predict, "_full_fingerprint",
            lambda path: in_fp if path.name == "in.mp4" else out_fp,
        )


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "in.mp4"
    a.touch()
    b = tmp_path / "out.mp4"
    b.touch()
    return a, b


def test_zero_duration_returns_zero(tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, duration=0, n_chunks=0, phashes_in=[], phashes_out=[])
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b)
    assert res.match_probability_self == 0.0
    assert res.chunks == []
    assert res.weakest_chunk is None


def test_identical_hashes_high_match(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, duration=12.0, n_chunks=3,
           phashes_in=[0xAAAA] * 3, phashes_out=[0xAAAA] * 3)
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b, chunk_sec=4.0)
    assert len(res.chunks) == 3
    assert res.match_probability_self > 0.99
    assert res.weakest_chunk is not None and res.weakest_chunk.combined > 0.99


def test_inverted_hashes_zero_visual(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # XOR with all-ones flips all 64 bits → distance 64 → similarity 0.
    inv = 0xFFFFFFFFFFFFFFFF
    _patch(monkeypatch, duration=12.0, n_chunks=3,
           phashes_in=[0] * 3, phashes_out=[inv] * 3)
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b, chunk_sec=4.0)
    for c in res.chunks:
        assert c.visual_similarity == 0.0
    assert res.match_probability_self == 0.0


def test_weakest_chunk_is_argmax(tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    inv = 0xFFFFFFFFFFFFFFFF
    # Chunk 0: identical (sim 1.0). Chunks 1,2: inverted (sim 0.0).
    _patch(monkeypatch, duration=12.0, n_chunks=3,
           phashes_in=[0xAAAA, 0, 0],
           phashes_out=[0xAAAA, inv, inv])
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b, chunk_sec=4.0)
    assert res.weakest_chunk is not None
    assert res.weakest_chunk.start_sec == 0.0
    assert res.weakest_chunk.combined > 0.99
    assert res.chunks[1].combined == 0.0


def test_no_corpus_means_no_matches(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, duration=4.0, n_chunks=1,
           phashes_in=[0xAA], phashes_out=[0xAA])
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b, corpus=None)
    assert res.corpus_matches == []


def test_audio_jaccard_per_chunk(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, duration=12.0, n_chunks=3,
           phashes_in=[0] * 3, phashes_out=[0xFFFFFFFFFFFFFFFF] * 3,
           in_fp=list(range(60)), out_fp=list(range(60)))
    a, b = _paths(tmp_path)
    res = cid_predict.predict(a, b, chunk_sec=4.0)
    # Visual all-zero, but audio identical → combined = audio similarity.
    for c in res.chunks:
        assert c.visual_similarity == 0.0
        assert c.audio_similarity > 0.99
        assert c.combined > 0.99
