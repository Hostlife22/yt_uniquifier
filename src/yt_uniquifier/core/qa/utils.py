"""Small helpers shared across QA modules."""

from __future__ import annotations

import base64


def _decode_chromaprint(b64: str) -> list[int]:
    """Decode chromaprint compressed base64 fingerprint to its 32-bit ints.

    The compressed form encodes the first hash directly then deltas. For our
    Jaccard purposes we don't need to undo the diffs — comparing raw bytes is
    enough as long as both sides use the same encoding.

    Returns a list of 4-byte chunks interpreted as uint32, after stripping the
    chromaprint 4-byte header (version + algorithm) if present.
    """
    raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    if len(raw) < 4:
        return []
    body = raw[4:]
    return [
        int.from_bytes(body[i : i + 4], "big", signed=False)
        for i in range(0, len(body) - len(body) % 4, 4)
    ]
