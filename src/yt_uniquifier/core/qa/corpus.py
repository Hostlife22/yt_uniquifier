"""Local fingerprint corpus.

A small JSON-backed index of files the user has already uploaded (or is
about to upload), so they can be checked against new outputs before a fresh
upload — answering "did I just produce something Content ID will match
against my own previous variant?"

Per entry we store:
  - phash_frames: 64-bit perceptual hashes for N evenly-spaced frames
  - audio_fingerprint: chromaprint subfingerprints (when fpcalc is available)

Both are JSON-serialisable ints; no numpy dependency.

Storage layout:
    ~/.cache/yt_uniquifier/corpus/
        index.json                 — all entries inline (single atomic write)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imagehash

from yt_uniquifier.core.qa import audio_fp, phash
from yt_uniquifier.core.qa.utils import _decode_chromaprint

DEFAULT_CORPUS_DIR = Path.home() / ".cache" / "yt_uniquifier" / "corpus"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CorpusEntry:
    id: str                          # first 16 hex of sha256(absolute path)
    path: Path
    added_at: float                  # unix timestamp
    duration_sec: float
    phash_frames: tuple[int, ...]    # 64-bit phashes
    audio_fingerprint: tuple[int, ...]   # chromaprint subfingerprints (or empty)
    sample_count: int                # len(phash_frames)


@dataclass(frozen=True)
class CorpusMatch:
    entry: CorpusEntry
    visual_similarity: float         # phash similarity vs target [0..1]
    audio_similarity: float          # chromaprint Jaccard vs target, 0 if either missing
    combined: float                  # max(visual, audio)


class Corpus:
    """JSON-backed fingerprint index for previously-known files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_CORPUS_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    # ---- public API --------------------------------------------------------

    def add(self, path: Path, *, samples: int = 60) -> CorpusEntry:
        """Fingerprint a file and add it to the index.

        Re-adding the same path replaces the previous entry. Returns the new
        entry.
        """
        if not path.exists():
            raise FileNotFoundError(path)
        entry_id = _hash_path(path)
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
            path=path.absolute(),
            added_at=time.time(),
            duration_sec=duration,
            phash_frames=phash_ints,
            audio_fingerprint=audio_ints,
            sample_count=len(phash_ints),
        )
        self._upsert(entry)
        return entry

    def remove(self, entry_id: str) -> bool:
        entries = self._load_all()
        new = [e for e in entries if e.id != entry_id]
        if len(new) == len(entries):
            return False
        self._save_all(new)
        return True

    def list_all(self) -> list[CorpusEntry]:
        return self._load_all()

    def search_match(
        self,
        target: Path,
        *,
        threshold: float = 0.5,
        samples: int = 60,
    ) -> list[CorpusMatch]:
        """Return entries whose combined similarity to target is >= threshold."""
        entries = self._load_all()
        if not entries:
            return []

        # Fingerprint the target once.
        target_frames = phash.sample_frames(target, n=samples)
        target_phashes = [int(str(imagehash.phash(f)), 16) for f in target_frames]

        target_audio: list[int] = []
        if audio_fp.fpcalc_available():
            raw = audio_fp._run_fpcalc(target)
            if raw and "fingerprint" in raw:
                try:
                    target_audio = _decode_chromaprint(str(raw["fingerprint"]))
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

    # ---- storage -----------------------------------------------------------

    def _load_all(self) -> list[CorpusEntry]:
        if not self.index_path.exists():
            return []
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries_raw = raw.get("entries", []) if isinstance(raw, dict) else []
        return [_entry_from_dict(d) for d in entries_raw]

    def _save_all(self, entries: list[CorpusEntry]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "entries": [_entry_to_dict(e) for e in entries],
        }
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def _upsert(self, entry: CorpusEntry) -> None:
        entries = self._load_all()
        new = [e for e in entries if e.id != entry.id]
        new.append(entry)
        self._save_all(new)


# ---- helpers --------------------------------------------------------------

def _hash_path(path: Path) -> str:
    return hashlib.sha256(str(path.absolute()).encode("utf-8")).hexdigest()[:16]


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


def _entry_to_dict(e: CorpusEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "path": str(e.path),
        "added_at": e.added_at,
        "duration_sec": e.duration_sec,
        "phash_frames": list(e.phash_frames),
        "audio_fingerprint": list(e.audio_fingerprint),
        "sample_count": e.sample_count,
    }


def _entry_from_dict(d: dict[str, Any]) -> CorpusEntry:
    return CorpusEntry(
        id=str(d["id"]),
        path=Path(d["path"]),
        added_at=float(d.get("added_at", 0.0)),
        duration_sec=float(d.get("duration_sec", 0.0)),
        phash_frames=tuple(int(x) for x in d.get("phash_frames", [])),
        audio_fingerprint=tuple(int(x) for x in d.get("audio_fingerprint", [])),
        sample_count=int(d.get("sample_count", 0)),
    )
