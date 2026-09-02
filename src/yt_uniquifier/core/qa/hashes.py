"""Streaming MD5 — never load the whole file into memory."""

from __future__ import annotations

import hashlib
from pathlib import Path


def md5_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    # The public report contract names this MD5. It is a diagnostic content
    # fingerprint, never an authenticity or signature decision.
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
