"""Unit tests for ``core.profile_marketplace`` (v0.9.0 R1 / F9).

The marketplace's whole trust story rests on three checks:

  1. HTTPS-only URLs (anything else refused before bytes are read).
  2. SHA-256 verification *before* the YAML lands at its final path.
  3. Pydantic schema validation *before* the YAML lands at its final
     path — even if the catalog SHA matches, a malformed profile must
     not pollute the per-user profile dir.

These tests force each gate to fail in turn, and exercise the happy
path end-to-end with a fake ``urlopen`` that serves bytes the test
controls. No real network hits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from yt_uniquifier.core import profile_marketplace as pm
from yt_uniquifier.core.profile_marketplace import (
    Catalog,
    CatalogEntry,
    MarketplaceError,
    fetch_catalog,
    find_entry,
    install,
    list_entries,
)

# ---------------------------------------------------------------------------
# Test fixtures: a valid YAML profile + its real SHA-256
# ---------------------------------------------------------------------------

VALID_PROFILE_YAML = b"""\
name: test-marketplace
description: tiny profile used by marketplace tests
transforms:
  - id: video.crop_pad
    enabled: true
    params: {}
seed_strategy: per_run
"""


def _sha(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


VALID_PROFILE_SHA = _sha(VALID_PROFILE_YAML)


def _make_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "test_entry",
        "name": "Test Entry",
        "description": "A test profile.",
        "url": "https://example.invalid/profiles/test.yaml",
        "sha256": VALID_PROFILE_SHA,
        "author": "tester",
        "tags": ["test"],
        "version": "1.0.0",
        "min_yt_uniquifier_version": "0.9.0",
    }
    base.update(overrides)
    return base


def _make_catalog_json(entries: list[dict[str, Any]] | None = None) -> bytes:
    return json.dumps({
        "version": 1,
        "entries": entries if entries is not None else [_make_entry()],
    }).encode("utf-8")


class _FakeResponse:
    """Stand-in for ``urllib.request.urlopen``'s context-managed result."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._data
        return self._data[:n]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, bytes],
) -> None:
    """Patch urlopen so each call returns ``payloads[url]`` or raises."""

    def fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in payloads:
            raise OSError(f"unexpected URL: {url}")
        return _FakeResponse(payloads[url])

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_catalog_entry_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_make_entry(unknown_field="boom"))


def test_catalog_entry_rejects_bad_sha_format() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_make_entry(sha256="too short"))


def test_catalog_entry_rejects_bad_id_chars() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_make_entry(id="../etc/passwd"))


def test_catalog_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Catalog.model_validate({
            "version": 1,
            "entries": [],
            "extra_thing": 42,
        })


# ---------------------------------------------------------------------------
# fetch_catalog
# ---------------------------------------------------------------------------


def test_fetch_catalog_refuses_non_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(MarketplaceError, match="non-HTTPS"):
        fetch_catalog(url="http://example.invalid/catalog.json", cache_dir=tmp_path)


def test_fetch_catalog_caches_then_serves_from_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _make_catalog_json()
    _install_urlopen(monkeypatch, {"https://example.invalid/catalog.json": body})

    cat1 = fetch_catalog(url="https://example.invalid/catalog.json", cache_dir=tmp_path)
    assert (tmp_path / "catalog.json").exists()
    assert len(cat1.entries) == 1

    # Second call must NOT hit the network; remove our payload and confirm.
    monkeypatch.setattr(pm.urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("urlopen called for cached fetch"),
    ))
    cat2 = fetch_catalog(url="https://example.invalid/catalog.json", cache_dir=tmp_path)
    assert cat2.entries[0].id == cat1.entries[0].id


def test_fetch_catalog_refresh_overrides_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body1 = _make_catalog_json([_make_entry(id="first")])
    body2 = _make_catalog_json([_make_entry(id="second")])
    _install_urlopen(monkeypatch, {"https://example.invalid/catalog.json": body1})
    fetch_catalog(url="https://example.invalid/catalog.json", cache_dir=tmp_path)

    _install_urlopen(monkeypatch, {"https://example.invalid/catalog.json": body2})
    cat = fetch_catalog(
        url="https://example.invalid/catalog.json",
        cache_dir=tmp_path,
        refresh=True,
    )
    assert cat.entries[0].id == "second"


def test_fetch_catalog_falls_back_to_cache_on_network_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _make_catalog_json()
    _install_urlopen(monkeypatch, {"https://example.invalid/catalog.json": body})
    fetch_catalog(url="https://example.invalid/catalog.json", cache_dir=tmp_path)

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("simulated network failure")

    monkeypatch.setattr(pm.urllib.request, "urlopen", boom)
    cat = fetch_catalog(
        url="https://example.invalid/catalog.json",
        cache_dir=tmp_path,
        refresh=True,
    )
    assert len(cat.entries) == 1


def test_fetch_catalog_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    huge = b"x" * (pm._MAX_CATALOG_BYTES + 2)
    _install_urlopen(monkeypatch, {"https://example.invalid/catalog.json": huge})
    with pytest.raises(MarketplaceError, match="exceeds"):
        fetch_catalog(url="https://example.invalid/catalog.json", cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# list_entries / find_entry
# ---------------------------------------------------------------------------


def test_list_entries_is_sorted_by_id() -> None:
    cat = Catalog.model_validate({
        "version": 1,
        "entries": [
            _make_entry(id="zeta"),
            _make_entry(id="alpha"),
            _make_entry(id="mike"),
        ],
    })
    ids = [e.id for e in list_entries(cat)]
    assert ids == ["alpha", "mike", "zeta"]


def test_find_entry_raises_on_miss() -> None:
    cat = Catalog.model_validate({"version": 1, "entries": []})
    with pytest.raises(MarketplaceError, match="no marketplace entry"):
        find_entry(cat, "nope")


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_happy_path_round_trips_through_profile_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = CatalogEntry.model_validate(_make_entry(
        url="https://example.invalid/profiles/test.yaml",
    ))
    _install_urlopen(monkeypatch, {
        "https://example.invalid/profiles/test.yaml": VALID_PROFILE_YAML,
    })

    result = install(entry, dest_dir=tmp_path)
    assert result.path == tmp_path / "test_entry.yaml"
    assert result.path.exists()
    assert result.profile_name == "test-marketplace"

    from yt_uniquifier.core.profile_loader import load_profile

    prof = load_profile(result.path)
    assert prof.name == "test-marketplace"


def test_install_rejects_sha_mismatch_and_does_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = CatalogEntry.model_validate(_make_entry(
        url="https://example.invalid/profiles/test.yaml",
        sha256="0" * 64,
    ))
    _install_urlopen(monkeypatch, {
        "https://example.invalid/profiles/test.yaml": VALID_PROFILE_YAML,
    })

    with pytest.raises(MarketplaceError, match="SHA-256 mismatch"):
        install(entry, dest_dir=tmp_path)
    # No file at the canonical name, no leftover partial in dest_dir.
    assert not (tmp_path / "test_entry.yaml").exists()
    assert not any(
        p.suffix == ".partial" or p.name.endswith(".yaml.partial")
        for p in tmp_path.iterdir()
    )


def test_install_rejects_invalid_yaml_after_sha_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_yaml = b"this: is\n  not: a profile: at all\n"
    entry = CatalogEntry.model_validate(_make_entry(
        url="https://example.invalid/profiles/bad.yaml",
        sha256=_sha(bad_yaml),
    ))
    _install_urlopen(monkeypatch, {
        "https://example.invalid/profiles/bad.yaml": bad_yaml,
    })

    with pytest.raises(MarketplaceError, match="schema validation"):
        install(entry, dest_dir=tmp_path)
    assert not (tmp_path / "test_entry.yaml").exists()


def test_install_refuses_to_overwrite_unless_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = CatalogEntry.model_validate(_make_entry(
        url="https://example.invalid/profiles/test.yaml",
    ))
    _install_urlopen(monkeypatch, {
        "https://example.invalid/profiles/test.yaml": VALID_PROFILE_YAML,
    })

    install(entry, dest_dir=tmp_path)
    with pytest.raises(MarketplaceError, match="refusing to overwrite"):
        install(entry, dest_dir=tmp_path)

    # overwrite=True succeeds.
    install(entry, dest_dir=tmp_path, overwrite=True)


def test_install_refuses_non_https_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = CatalogEntry.model_validate(_make_entry(
        url="http://example.invalid/profiles/test.yaml",
    ))
    with pytest.raises(MarketplaceError, match="non-HTTPS"):
        install(entry, dest_dir=tmp_path)


def test_install_oversized_response_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    huge = b"x" * (pm._MAX_PROFILE_BYTES + 2)
    entry = CatalogEntry.model_validate(_make_entry(
        url="https://example.invalid/profiles/huge.yaml",
        sha256=_sha(huge),
    ))
    _install_urlopen(monkeypatch, {
        "https://example.invalid/profiles/huge.yaml": huge,
    })
    with pytest.raises(MarketplaceError, match="exceeds"):
        install(entry, dest_dir=tmp_path)


# ---------------------------------------------------------------------------
# Bootstrap catalog (shipped in the wheel)
# ---------------------------------------------------------------------------


def test_bootstrap_catalog_is_valid_and_https() -> None:
    """The catalog.json bundled in the wheel must parse and be sane."""
    raw = pm.BOOTSTRAP_CATALOG_PATH.read_bytes()
    cat = pm._parse_catalog(raw, source=str(pm.BOOTSTRAP_CATALOG_PATH))
    assert cat.entries, "bootstrap catalog should not be empty"
    for entry in cat.entries:
        assert entry.url.startswith("https://"), (
            f"bootstrap entry {entry.id} url must be https"
        )
        # sha256 format already enforced by the model — assert anyway
        # for explicit failure messages.
        assert len(entry.sha256) == 64
