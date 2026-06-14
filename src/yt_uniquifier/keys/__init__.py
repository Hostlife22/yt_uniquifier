"""Bundled public keys for yt-uniquifier supply-chain verification.

v1.2.0 Task 25 — the marketplace operator signs each catalog entry's
profile-body SHA-256 with an Ed25519 key; the bundled public keys here
verify those signatures.  See ``docs/marketplace.md`` for the signing
workflow and key rotation policy.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

KEY_PACKAGE = "yt_uniquifier.keys"
MARKETPLACE_KEY_FILENAME = "marketplace.pub"


def marketplace_public_key_path() -> Path:
    """Return the on-disk path of the bundled marketplace public key.

    ``importlib.resources.files`` resolves the path correctly across
    source installs, wheel installs, and PyInstaller frozen builds.
    """
    return Path(str(resources.files(KEY_PACKAGE).joinpath(MARKETPLACE_KEY_FILENAME)))


def load_marketplace_public_keys() -> tuple[bytes, ...]:
    """Return every raw 32-byte Ed25519 public key listed in the bundle.

    Lines starting with ``#`` are comments; every non-comment line must
    be a 64-char lowercase hex string.  Multiple keys are listed during
    rotation so an in-flight signed catalog continues to verify against
    the old key until every entry is re-signed.

    Raises ``ValueError`` when the file is malformed or empty.
    """
    path = marketplace_public_key_path()
    raw_text = path.read_text(encoding="utf-8")
    keys: list[bytes] = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            key_bytes = bytes.fromhex(stripped)
        except ValueError as exc:
            raise ValueError(
                f"marketplace key file {path} line {line_no}: not hex ({exc})"
            ) from exc
        if len(key_bytes) != 32:
            raise ValueError(
                f"marketplace key file {path} line {line_no}: "
                f"{len(key_bytes)} bytes; Ed25519 keys are 32 bytes"
            )
        keys.append(key_bytes)
    if not keys:
        raise ValueError(f"marketplace key file {path} contains no key")
    return tuple(keys)
