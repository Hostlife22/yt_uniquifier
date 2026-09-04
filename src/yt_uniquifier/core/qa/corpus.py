"""Local fingerprint corpus — facade over :mod:`corpus_db`.

A small local index of owned or licensed files and their derivatives.  It supports
self-collision/regression diagnostics without claiming to predict the result of an
external rights-management system.

Storage moved from a single ``index.json`` to a per-row SQLite table in
v0.8.0 (see :mod:`corpus_db` for the rationale + schema). This module
keeps the older method names (``add`` / ``list_all`` / ``remove`` /
``search_match``) so every existing CLI / GUI / test callsite continues
to work; new code should prefer ``corpus_db.CorpusDB`` directly.

``CorpusEntry`` is re-exported from ``corpus_db`` so older imports like
``from yt_uniquifier.core.qa.corpus import CorpusEntry`` still resolve.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import imagehash

from yt_uniquifier.core.qa import audio_fp, phash
from yt_uniquifier.core.qa.corpus_db import (
    LEGACY_JSON_FILENAME,
    SCHEMA_VERSION,
    SQLITE_FILENAME,
    CorpusDB,
    CorpusEntry,
    migrate_from_json,
)
from yt_uniquifier.core.qa.utils import _decode_chromaprint

DEFAULT_CORPUS_DIR = Path.home() / ".cache" / "yt_uniquifier" / "corpus"

__all__ = [
    "DEFAULT_CORPUS_DIR",
    "LEGACY_JSON_FILENAME",
    "SCHEMA_VERSION",
    "SQLITE_FILENAME",
    "Corpus",
    "CorpusDB",
    "CorpusEntry",
    "CorpusMatch",
    "migrate_from_json",
]


@dataclass(frozen=True)
class CorpusMatch:
    entry: CorpusEntry
    visual_similarity: float         # phash similarity vs target [0..1]
    audio_similarity: float          # chromaprint Jaccard vs target, 0 if either missing
    combined: float                  # max(visual, audio)


class Corpus:
    """Fingerprint index facade. Storage lives in ``corpus_db.CorpusDB``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_CORPUS_DIR
        self._db = CorpusDB(self.root)

    # ---- public API --------------------------------------------------------

    def add(self, path: Path, *, samples: int = 60) -> CorpusEntry:
        """Fingerprint a file and add it to the index.

        Re-adding the same path replaces the previous entry. Returns the new
        entry.
        """
        if not path.exists():
            raise FileNotFoundError(path)
        resolved_path = path.resolve()
        stat = resolved_path.stat()
        content_sha256 = _hash_file(resolved_path)
        entry_id = content_sha256[:16]
        existing = self._db.lookup_by_id(entry_id)
        if existing is not None and existing.content_sha256 not in {
            None, content_sha256,
        }:
            # A 64-bit prefix collision is extremely unlikely, but never let it
            # silently alias two licensed assets. Expand only the colliding ID.
            entry_id = content_sha256[:32]
            existing = self._db.lookup_by_id(entry_id)
        if (
            existing is not None
            and existing.content_sha256 == content_sha256
            and existing.sample_count == samples
        ):
            moved = CorpusEntry(
                id=existing.id,
                path=resolved_path,
                added_at=existing.added_at,
                duration_sec=existing.duration_sec,
                phash_frames=existing.phash_frames,
                audio_fingerprint=existing.audio_fingerprint,
                sample_count=existing.sample_count,
                content_sha256=content_sha256,
                stat_size=stat.st_size,
                stat_mtime_ns=stat.st_mtime_ns,
            )
            self._db.add_entry(moved)
            return moved

        duration = phash._probe_duration(path)
        frames = phash.sample_frames(path, n=samples)
        phash_ints = tuple(int(str(imagehash.phash(f)), 16) for f in frames)

        audio_ints: tuple[int, ...] = ()
        if audio_fp.fpcalc_available():
            raw = audio_fp._run_fpcalc(path)
            if raw and "fingerprint" in raw:
                try:
                    audio_ints = tuple(_decode_chromaprint(str(raw["fingerprint"])))
                except ValueError:
                    audio_ints = ()

        entry = CorpusEntry(
            id=entry_id,
            path=resolved_path,
            added_at=time.time(),
            duration_sec=duration,
            phash_frames=phash_ints,
            audio_fingerprint=audio_ints,
            sample_count=len(phash_ints),
            content_sha256=content_sha256,
            stat_size=stat.st_size,
            stat_mtime_ns=stat.st_mtime_ns,
        )
        self._db.add_entry(entry)
        return entry

    def remove(self, entry_id: str) -> bool:
        return self._db.purge(entry_id)

    def list_all(self) -> list[CorpusEntry]:
        return list(self._db.iter_entries())

    def search_match(
        self,
        target: Path,
        *,
        threshold: float = 0.5,
        samples: int = 60,
        target_phashes: list[int] | None = None,
        target_audio: list[int] | None = None,
    ) -> list[CorpusMatch]:
        """Return entries whose combined similarity to target is >= threshold.

        ``target_phashes`` / ``target_audio`` may be supplied by callers
        that have already fingerprinted the target (e.g.
        ``cid_predict.predict``) — without that, the QA report path
        fingerprints the same output file twice (once for cid_predict,
        once for this search). Each ffmpeg/fpcalc sample pass is
        seconds; saving a second pass is a useful win on every report.
        """
        entries = self.list_all()
        if not entries:
            return []

        if target_phashes is None:
            target_frames = phash.sample_frames(target, n=samples)
            target_phashes = [
                int(str(imagehash.phash(f)), 16) for f in target_frames
            ]

        if target_audio is None:
            target_audio = []
            if audio_fp.fpcalc_available():
                raw = audio_fp._run_fpcalc(target)
                if raw and "fingerprint" in raw:
                    try:
                        target_audio = _decode_chromaprint(
                            str(raw["fingerprint"])
                        )
                    except ValueError:
                        target_audio = []

        matches: list[CorpusMatch] = []
        for e in entries:
            vis = _phash_similarity(e.phash_frames, target_phashes)
            aud = (
                _jaccard(set(e.audio_fingerprint), set(target_audio))
                if e.audio_fingerprint and target_audio
                else 0.0
            )
            combined = max(vis, aud)
            if combined >= threshold:
                matches.append(CorpusMatch(
                    entry=e, visual_similarity=vis,
                    audio_similarity=aud, combined=combined,
                ))
        matches.sort(key=lambda m: m.combined, reverse=True)
        return matches


# ---- helpers --------------------------------------------------------------


def _hash_path(path: Path) -> str:
    """Legacy path-derived ID helper retained for old tests/importers."""
    return hashlib.sha256(str(path.absolute()).encode("utf-8")).hexdigest()[:16]


def _hash_file(path: Path) -> str:
    """Return a streaming content identity without loading large media in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _phash_similarity(a: tuple[int, ...] | list[int], b: list[int]) -> float:
    """Mean per-frame pHash similarity over min(len(a), len(b)) pairs."""
    pairs = min(len(a), len(b))
    if pairs == 0:
        return 0.0
    dists = [bin(a[i] ^ b[i]).count("1") for i in range(pairs)]
    mean_dist = sum(dists) / pairs
    return max(0.0, 1.0 - mean_dist / 64.0)


def _jaccard(s1: set[int], s2: set[int]) -> float:
    union = len(s1 | s2)
    return len(s1 & s2) / union if union else 0.0
