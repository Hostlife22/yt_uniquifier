"""Community profile marketplace (v0.9.0 R1 / F9).

A small, audit-able client that lists and installs YAML profiles
contributed by the community without ever executing untrusted code:

  * The catalog lives at a single pinned HTTPS URL (a JSON file on the
    `yt-uniquifier-profiles/` GitHub repo). It is fetched, validated
    against a strict pydantic schema (``extra="forbid"``), and cached
    under ``~/.cache/yt_uniquifier/marketplace/`` so repeated CLI/GUI
    calls do not re-download.
  * Each catalog entry carries a SHA-256 of the profile YAML it points
    to. ``install`` refuses to write the file if the hash does not
    match — the same trust model used for the SSCD weights in
    ``core/qa/sscd.py``. A profile that fails the schema check is
    rejected *before* it lands on disk.
  * Profiles are declarative YAML; loading one only invokes the
    pydantic schema in ``core/profile_loader.py``. There is no path by
    which a malicious entry could execute code on the user's machine
    short of poisoning both the catalog and a matching SHA — and even
    then the result is still parsed by ``yaml.safe_load`` and the
    profile schema's ``extra="forbid"`` guard.

URLs are restricted to ``https://`` to prevent surprise downgrades
(``file://`` reads would let a poisoned catalog escalate against
shared filesystems; ``http://`` is a no-go on a feature whose entire
trust story rests on TLS + SHA pinning).

The CLI surface lives in ``cli/cmd_profile.py``; the GUI worker in
``gui/workers/marketplace_worker.py``. Both call the four public
functions in this module: ``fetch_catalog``, ``list_entries``,
``install``, ``default_install_dir``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import ProfileLoadError, load_profile

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pinned defaults
# ---------------------------------------------------------------------------

# The long-term home for the catalog. The repo is intentionally
# separate from yt-uniquifier proper so adding a community profile is a
# pull request against a tiny YAML+JSON repo rather than the main
# codebase. Until the repo exists, the in-tree ``marketplace/
# catalog.json`` bootstraps the same shape (see the seed file and
# ``BOOTSTRAP_CATALOG_PATH`` below).
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yt-uniquifier/"
    "yt-uniquifier-profiles/main/catalog.json"
)

# Bootstrap fallback shipped with the wheel under
# ``src/yt_uniquifier/marketplace/catalog.json``. Used only when the
# network is unreachable AND no cached catalog exists; mirrors
# ``gui.paths.profiles_dir`` so the file survives PyInstaller and
# zipapp packaging.
def _bootstrap_catalog_path() -> Path:
    return Path(str(files("yt_uniquifier").joinpath("marketplace/catalog.json")))


BOOTSTRAP_CATALOG_PATH = _bootstrap_catalog_path()

_CACHE_FILENAME = "catalog.json"
_USER_AGENT = "yt-uniquifier-marketplace/1.0"
_NETWORK_TIMEOUT_SEC = 15.0
_MAX_PROFILE_BYTES = 1 * 1024 * 1024  # 1 MiB — generous for YAML
_MAX_CATALOG_BYTES = 4 * 1024 * 1024  # 4 MiB — room for thousands of entries


def default_cache_dir() -> Path:
    """Local catalog cache; mirrors ``core/qa/sscd.py`` defaults."""
    return Path.home() / ".cache" / "yt_uniquifier" / "marketplace"


def default_install_dir() -> Path:
    """Per-user profile directory used by the CLI/GUI installers.

    Mirrors the XDG convention; the GUI may override with
    ``QStandardPaths.AppConfigLocation`` (see ``gui/paths.py``).
    """
    return Path.home() / ".config" / "yt_uniquifier" / "profiles"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MarketplaceError(YtUniquifierError):
    """Catalog fetch, validation, or install failure."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class CatalogEntry(BaseModel):
    """One installable community profile."""

    model_config = ConfigDict(extra="forbid")

    # ``id`` must be filename-safe; we use it as ``<id>.yaml`` on disk.
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    author: str = Field(default="anonymous", max_length=128)
    tags: list[str] = Field(default_factory=list)
    version: str = Field(default="0.1.0", max_length=32)
    # Lower bound on the yt-uniquifier release this profile targets.
    # Empty / missing means "any". A semver-aware compatibility gate is
    # a v1.0 feature; for now the CLI surfaces this verbatim.
    min_yt_uniquifier_version: str = Field(default="", max_length=32)
    # v1.2.0 Task 25 — Ed25519 signature over ``sha256`` (the profile-body
    # hash) produced by the marketplace operator's offline private key.
    # 64 raw bytes hex-encoded → 128 hex characters.  ``None`` for legacy
    # unsigned entries; ``install(..., require_signature=True)`` rejects
    # those.  Verification uses the bundled public key set at
    # ``src/yt_uniquifier/keys/marketplace.pub``.
    signature: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{128}$",
    )


class Catalog(BaseModel):
    """Root document fetched from ``DEFAULT_CATALOG_URL``."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, le=99)
    entries: list[CatalogEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class InstallResult:
    """Return shape of ``install`` — what landed where, plus the loaded profile name."""

    entry_id: str
    path: Path
    profile_name: str


# ---------------------------------------------------------------------------
# Catalog I/O
# ---------------------------------------------------------------------------


def _is_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


class _NetworkFailure(MarketplaceError):
    """Internal marker for failures that allow cache/bootstrap fallback.

    Recovery is only safe for *transport* errors (DNS, TCP, TLS, HTTP
    error responses). Config-shape problems (non-HTTPS URL,
    oversized payload, parse failure) propagate as plain
    ``MarketplaceError`` so callers see the real issue.
    """


def _validate_url(url: str) -> None:
    if not _is_https(url):
        raise MarketplaceError(
            f"refusing non-HTTPS marketplace URL: {url!r}"
        )


def _fetch_bytes(url: str, *, max_bytes: int) -> bytes:
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        # ``urlopen`` returns a context-managed response; we trust only
        # the schema check above, never follow file:// schemes via
        # redirect (urllib's default opener honours that constraint).
        with urllib.request.urlopen(  # noqa: S310 — HTTPS-only enforced above
            req,
            timeout=_NETWORK_TIMEOUT_SEC,
        ) as resp:
            # Length-cap so a hostile server can't OOM us by streaming
            # a multi-GB response. We read max_bytes+1 to disambiguate
            # "exactly at limit" from "exceeded".
            data: bytes = resp.read(max_bytes + 1)
    except OSError as exc:
        # Transport-level failure — allow fallback.
        raise _NetworkFailure(f"network error fetching {url}: {exc}") from exc
    if len(data) > max_bytes:
        # Hostile / misconfigured server — do NOT silently fall back to
        # a stale cache.
        raise MarketplaceError(
            f"response from {url} exceeds {max_bytes} bytes (got > {max_bytes})"
        )
    return data


def _parse_catalog(raw: bytes, *, source: str) -> Catalog:
    try:
        doc: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketplaceError(
            f"catalog at {source} is not valid UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise MarketplaceError(
            f"catalog at {source} root must be a JSON object, got {type(doc).__name__}"
        )
    try:
        return Catalog.model_validate(doc)
    except ValidationError as exc:
        raise MarketplaceError(
            f"catalog at {source} failed schema validation: {exc}"
        ) from exc


def fetch_catalog(
    *,
    url: str = DEFAULT_CATALOG_URL,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> Catalog:
    """Return the parsed catalog, using the on-disk cache when possible.

    - ``refresh=False`` (default): return the cached copy if present;
      otherwise fetch + cache + return.
    - ``refresh=True``: re-fetch unconditionally and overwrite the cache.

    If the network fetch fails AND no cache exists, fall back to the
    in-tree bootstrap catalog so the CLI/GUI are usable offline on a
    fresh install. The bootstrap path is logged so users see why their
    catalog is small.
    """
    # Validate URL up-front so a non-HTTPS catalog config is a hard
    # error regardless of cache state — silently serving a stale
    # cached catalog for a misconfigured URL would mask the bug.
    _validate_url(url)

    cache_root = cache_dir or default_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / _CACHE_FILENAME

    if not refresh and cache_path.exists():
        return _parse_catalog(cache_path.read_bytes(), source=str(cache_path))

    try:
        data = _fetch_bytes(url, max_bytes=_MAX_CATALOG_BYTES)
        catalog = _parse_catalog(data, source=url)
    except _NetworkFailure as exc:
        # Transport-level failure only: try cache, then bootstrap, then
        # re-raise. Config-shape errors (oversize, parse, schema)
        # propagate untouched.
        if cache_path.exists():
            _log.warning(
                "catalog refresh failed (%s) — using cached copy at %s",
                exc, cache_path,
            )
            return _parse_catalog(cache_path.read_bytes(), source=str(cache_path))
        if BOOTSTRAP_CATALOG_PATH.exists():
            _log.warning(
                "catalog refresh failed (%s) — falling back to bootstrap %s",
                exc, BOOTSTRAP_CATALOG_PATH,
            )
            return _parse_catalog(
                BOOTSTRAP_CATALOG_PATH.read_bytes(),
                source=str(BOOTSTRAP_CATALOG_PATH),
            )
        raise

    # Atomic write: temp file + replace. Same pattern as
    # ``CheckpointStore._flush`` and ``profile_loader.dump_profile``.
    tmp = cache_path.with_suffix(".json.partial")
    tmp.write_bytes(data)
    tmp.replace(cache_path)
    return catalog


def list_entries(catalog: Catalog) -> list[CatalogEntry]:
    """Stable-sorted view (by ``id``) for table rendering."""
    return sorted(catalog.entries, key=lambda e: e.id)


def find_entry(catalog: Catalog, entry_id: str) -> CatalogEntry:
    """Lookup by id; raises MarketplaceError on miss."""
    for entry in catalog.entries:
        if entry.id == entry_id:
            return entry
    raise MarketplaceError(f"no marketplace entry with id {entry_id!r}")


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _verify_signature(
    *,
    sha256_hex: str,
    signature_hex: str,
    public_keys: tuple[bytes, ...],
) -> bool:
    """Return True iff ``signature_hex`` is a valid Ed25519 signature
    over the ASCII bytes of ``sha256_hex`` under any key in ``public_keys``.

    Multiple keys are accepted to support rotation: the new key is
    pushed to the front of the bundle and the old key kept until every
    in-flight catalog entry has been re-signed.

    Imports ``cryptography`` lazily so the marketplace module stays
    importable on installs that haven't pulled the optional crypto
    dependency yet (signature support is opt-in via
    ``require_signature=True`` or env var
    ``YT_UNIQ_REQUIRE_SIGNED_PROFILES=1``).
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise MarketplaceError(
            "signature verification requires the 'cryptography' package "
            f"({exc}).  Install yt-uniquifier with the [crypto] extra or "
            "drop YT_UNIQ_REQUIRE_SIGNED_PROFILES."
        ) from exc
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    if len(signature) != 64:
        return False
    payload = sha256_hex.encode("ascii")
    for key_bytes in public_keys:
        try:
            verifier = ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
            verifier.verify(signature, payload)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def install(
    entry: CatalogEntry,
    *,
    dest_dir: Path | None = None,
    overwrite: bool = False,
    require_signature: bool | None = None,
    public_keys: tuple[bytes, ...] | None = None,
) -> InstallResult:
    """Download, verify, validate, and write a community profile.

    Returns the install path on success. Raises ``MarketplaceError`` on:
      * non-HTTPS ``entry.url``
      * network failure or oversized response
      * SHA-256 mismatch (the file is **not** written; we never persist
        unverified bytes)
      * v1.2.0 Task 25 — when signature enforcement is on (param
        ``require_signature=True`` or env ``YT_UNIQ_REQUIRE_SIGNED_PROFILES=1``):
        an entry with no ``signature``, or whose signature does not
        verify against any bundled marketplace public key
      * profile schema validation failure
      * existing target path when ``overwrite=False``

    Side effects: creates ``dest_dir`` if missing; writes the YAML
    atomically via tempfile + ``os.replace``.

    ``require_signature``:
      * ``None`` (default) — read from env ``YT_UNIQ_REQUIRE_SIGNED_PROFILES``.
      * ``True`` — reject unsigned and invalid-signature entries.
      * ``False`` — keep the pre-v1.2 behaviour (SHA only).
    """
    target_dir = dest_dir or default_install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{entry.id}.yaml"
    if target_path.exists() and not overwrite:
        raise MarketplaceError(
            f"refusing to overwrite existing {target_path} (pass overwrite=True)"
        )

    # Resolve enforcement policy.
    if require_signature is None:
        import os as _os
        require_signature = _os.environ.get(
            "YT_UNIQ_REQUIRE_SIGNED_PROFILES", "",
        ) == "1"
    if require_signature and entry.signature is None:
        raise MarketplaceError(
            f"entry {entry.id!r} is unsigned but signature enforcement is "
            "on (require_signature=True or YT_UNIQ_REQUIRE_SIGNED_PROFILES=1). "
            "Ask the catalog operator to sign this entry, or drop the "
            "enforcement flag for unsigned-trust mode."
        )
    if require_signature and entry.signature is not None:
        # Lazy-load the bundled keys only when we're actually enforcing,
        # so installs without the [crypto] extra still work for unsigned
        # catalogs.
        if public_keys is None:
            from yt_uniquifier.keys import load_marketplace_public_keys
            public_keys = load_marketplace_public_keys()
        if not _verify_signature(
            sha256_hex=entry.sha256,
            signature_hex=entry.signature,
            public_keys=public_keys,
        ):
            raise MarketplaceError(
                f"signature on entry {entry.id!r} did not verify against any "
                f"bundled marketplace public key ({len(public_keys)} keys "
                "tried).  Refusing to install."
            )

    data = _fetch_bytes(entry.url, max_bytes=_MAX_PROFILE_BYTES)
    actual_sha = _sha256_bytes(data)
    if actual_sha.lower() != entry.sha256.lower():
        raise MarketplaceError(
            f"SHA-256 mismatch for {entry.id}: got {actual_sha}, "
            f"expected {entry.sha256}. Refusing to install."
        )

    # Stage to a temp file in the *same directory* so the final
    # ``replace`` is atomic on POSIX and best-effort on Windows. We
    # also validate the profile *before* the rename so a broken YAML
    # never reaches its final filename.
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{entry.id}.",
        suffix=".yaml.partial",
        dir=str(target_dir),
        delete=False,
    ) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        profile = load_profile(tmp_path)
    except ProfileLoadError as exc:
        tmp_path.unlink(missing_ok=True)
        raise MarketplaceError(
            f"downloaded profile {entry.id} failed schema validation: {exc}"
        ) from exc

    try:
        tmp_path.replace(target_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise MarketplaceError(
            f"failed to write {target_path}: {exc}"
        ) from exc

    return InstallResult(
        entry_id=entry.id,
        path=target_path,
        profile_name=profile.name,
    )


def purge_cache(cache_dir: Path | None = None) -> None:
    """Remove the catalog cache; the next ``fetch_catalog`` refetches.

    Convenience for the CLI and for tests; safe to call when the cache
    directory does not exist.
    """
    root = cache_dir or default_cache_dir()
    if root.exists():
        shutil.rmtree(root)
