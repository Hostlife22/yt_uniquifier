"""Resolve `run_seed` from a Profile + SourceMeta per its seed_strategy."""

from __future__ import annotations

import hashlib
import random

from yt_uniquifier.core.models import Profile, SourceMeta

_UINT32_MAX = 2**32


def resolve_run_seed(profile: Profile, source: SourceMeta) -> int:
    """Pick the seed for this invocation according to profile.seed_strategy.

    - `fixed`    → `profile.seed` verbatim (or 0 if None).
    - `per_file` → deterministic hash of the source path string.
    - `per_run`  → fresh random number; each invocation differs.

    The result is always a uint32 so it round-trips through JSON cleanly.
    """
    if profile.seed_strategy == "fixed":
        return (profile.seed or 0) % _UINT32_MAX
    if profile.seed_strategy == "per_file":
        digest = hashlib.sha256(str(source.path).encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big", signed=False)
    # per_run
    return random.randrange(_UINT32_MAX)
