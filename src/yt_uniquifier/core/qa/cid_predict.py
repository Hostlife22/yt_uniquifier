"""Chunked similarity predictor — closer to how Content ID actually matches.

Content ID looks at overlapping ~4-second windows. Averaging similarity
across the whole file hides the weakest chunk where a match would actually
trigger. We measure per-chunk and take the maximum.

For each chunk:
  visual = pHash similarity between one frame from each file
  audio  = chromaprint Jaccard over chunk's slice of the fingerprint
combined = max(visual, audio)

match_probability_self = max(combined) over all chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import imagehash

from yt_uniquifier.core.qa import audio_fp, phash
from yt_uniquifier.core.qa.corpus import Corpus, CorpusMatch
from yt_uniquifier.core.qa.utils import _decode_chromaprint


@dataclass(frozen=True)
class ChunkSimilarity:
    start_sec: float
    end_sec: float
    visual_similarity: float    # 0..1
    audio_similarity: float     # 0..1; 0 if either side lacks fp
    combined: float             # max of the two


@dataclass(frozen=True)
class CIDPredictResult:
    match_probability_self: float
    chunks: list[ChunkSimilarity]
    weakest_chunk: ChunkSimilarity | None
    corpus_matches: list[CorpusMatch]


def predict(
    input_path: Path,
    output_path: Path,
    *,
    chunk_sec: float = 4.0,
    corpus: Corpus | None = None,
    corpus_threshold: float = 0.3,
) -> CIDPredictResult:
    """Compute per-chunk similarity input↔output, plus optional corpus search."""
    duration = min(phash._probe_duration(input_path), phash._probe_duration(output_path))
    if duration <= 0:
        return CIDPredictResult(0.0, [], None, [])

    n_chunks = max(1, int(duration // chunk_sec))

    # ---- visual: sample one frame per chunk ----
    in_frames = phash.sample_frames(input_path, n=n_chunks)
    out_frames = phash.sample_frames(output_path, n=n_chunks)
    in_phashes = [int(str(imagehash.phash(f)), 16) for f in in_frames]
    out_phashes = [int(str(imagehash.phash(f)), 16) for f in out_frames]
    n_pairs = min(len(in_phashes), len(out_phashes), n_chunks)

    # ---- audio: full fingerprint, sliced into chunks ----
    in_fp = _full_fingerprint(input_path)
    out_fp = _full_fingerprint(output_path)
    audio_per_chunk = _per_chunk_audio_similarity(in_fp, out_fp, n_pairs)

    chunks: list[ChunkSimilarity] = []
    for i in range(n_pairs):
        vis = _phash_pair_similarity(in_phashes[i], out_phashes[i])
        aud = audio_per_chunk[i]
        combined = max(vis, aud)
        chunks.append(ChunkSimilarity(
            start_sec=i * chunk_sec,
            end_sec=(i + 1) * chunk_sec,
            visual_similarity=vis,
            audio_similarity=aud,
            combined=combined,
        ))

    if not chunks:
        match_prob = 0.0
        weakest: ChunkSimilarity | None = None
    else:
        match_prob = max(c.combined for c in chunks)
        # `weakest_chunk` is the chunk where our CID-evasion defence is
        # weakest — i.e. the chunk most similar to the source, which is
        # also where Content ID is most likely to fire. So it is argmax
        # of `combined`, not argmin. Naming preserved for QAReport
        # field compatibility (see docs/qa_report.md).
        weakest = max(chunks, key=lambda c: c.combined)

    corpus_matches: list[CorpusMatch] = []
    if corpus is not None:
        corpus_matches = corpus.search_match(output_path, threshold=corpus_threshold)

    return CIDPredictResult(
        match_probability_self=match_prob,
        chunks=chunks,
        weakest_chunk=weakest,
        corpus_matches=corpus_matches,
    )


# ---- helpers --------------------------------------------------------------

def _phash_pair_similarity(a: int, b: int) -> float:
    dist = bin(a ^ b).count("1")
    return max(0.0, 1.0 - dist / 64.0)


def _full_fingerprint(path: Path) -> list[int]:
    if not audio_fp.fpcalc_available():
        return []
    raw = audio_fp._run_fpcalc(path)
    if not raw or "fingerprint" not in raw:
        return []
    try:
        return _decode_chromaprint(str(raw["fingerprint"]))
    except ValueError:
        return []


def _per_chunk_audio_similarity(
    a: list[int], b: list[int], n_chunks: int,
) -> list[float]:
    """Slice each fingerprint into n_chunks ~equal pieces, Jaccard per slice."""
    if not a or not b or n_chunks <= 0:
        return [0.0] * n_chunks
    if n_chunks == 1:
        return [_jaccard(set(a), set(b))]
    sims: list[float] = []
    chunk_a = max(1, len(a) // n_chunks)
    chunk_b = max(1, len(b) // n_chunks)
    for i in range(n_chunks):
        slice_a = set(a[i * chunk_a : (i + 1) * chunk_a])
        slice_b = set(b[i * chunk_b : (i + 1) * chunk_b])
        sims.append(_jaccard(slice_a, slice_b))
    return sims


def _jaccard(s1: set[int], s2: set[int]) -> float:
    union = len(s1 | s2)
    return len(s1 & s2) / union if union else 0.0
