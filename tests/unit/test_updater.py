"""v1.3.0 Task 33 — updater unit tests.

Covers:

  * semver comparison: dotted-numeric > suffix > string
  * manifest schema rejects unknown fields + bad sha format
  * check_for_update collapses transport errors to available=False
  * YT_UNIQ_DISABLE_UPDATER=1 short-circuits the check
  * non-HTTPS URL rejected
  * downgrade refused in apply_update
  * platform key mismatch raises
  * sha mismatch raises and removes file
  * cosign missing raises UpdaterError (no silent trust)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yt_uniquifier.core import updater
from yt_uniquifier.core.updater import (
    UpdateAsset,
    UpdateManifest,
    UpdaterError,
    _semver_greater,
    check_for_update,
)

# ---------------------------------------------------------------------------
# semver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("a, b, expected", [
    ("1.3.0", "1.2.0", True),
    ("1.2.0", "1.3.0", False),
    ("1.2.10", "1.2.9", True),
    ("v1.3.0", "1.2.0", True),
    ("1.2.0", "1.2.0", False),
    ("1.2.0+source", "1.2.0", False),  # +source equals 1.2.0 numerically
])
def test_semver_greater(a: str, b: str, expected: bool) -> None:
    assert _semver_greater(a, b) is expected


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


def test_update_asset_rejects_bad_sha() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        UpdateAsset.model_validate({
            "platform": "linux",
            "url": "https://example.invalid/asset",
            "sha256": "not-a-hex-string",
            "cosign_bundle_url": "https://example.invalid/asset.bundle",
        })


def test_update_manifest_rejects_unknown_field() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        UpdateManifest.model_validate({
            "version": "1.3.0",
            "assets": [],
            "extra_unknown_field": True,
        })


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


def test_check_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YT_UNIQ_DISABLE_UPDATER", "1")
    result = check_for_update()
    assert result.available is False
    assert result.current_version == result.latest_version


def test_check_transport_error_returns_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_url: str) -> UpdateManifest:
        raise UpdaterError("network down")

    monkeypatch.setattr(updater, "_fetch_manifest", boom)
    monkeypatch.delenv("YT_UNIQ_DISABLE_UPDATER", raising=False)
    result = check_for_update()
    assert result.available is False


def test_check_newer_version_marks_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = UpdateManifest(
        version="99.0.0", notes_url="https://example.invalid/notes",
        assets=[],
    )
    monkeypatch.setattr(updater, "_fetch_manifest", lambda _u: fake)
    monkeypatch.delenv("YT_UNIQ_DISABLE_UPDATER", raising=False)
    result = check_for_update()
    assert result.available is True
    assert result.latest_version == "99.0.0"
    assert result.notes_url == "https://example.invalid/notes"


# ---------------------------------------------------------------------------
# Network gate
# ---------------------------------------------------------------------------


def test_fetch_manifest_refuses_non_https() -> None:
    with pytest.raises(UpdaterError, match="non-HTTPS"):
        updater._fetch_manifest("http://example.invalid/manifest.json")


# ---------------------------------------------------------------------------
# apply_update
# ---------------------------------------------------------------------------


def test_apply_update_refuses_downgrade(tmp_path: Path) -> None:
    manifest = UpdateManifest(version="0.0.1", assets=[
        UpdateAsset(
            platform="linux", url="https://example.invalid/x.tar.gz",
            sha256="0" * 64,
            cosign_bundle_url="https://example.invalid/x.bundle",
        ),
    ])
    with pytest.raises(UpdaterError, match="downgrade"):
        updater.apply_update(manifest, dest_dir=tmp_path)


def test_apply_update_missing_platform_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    manifest = UpdateManifest(version="99.0.0", assets=[
        UpdateAsset(
            platform="windows", url="https://example.invalid/x.zip",
            sha256="0" * 64,
            cosign_bundle_url="https://example.invalid/x.bundle",
        ),
    ])
    with pytest.raises(UpdaterError, match="no asset for platform"):
        updater.apply_update(manifest, platform_key="linux", dest_dir=tmp_path)


def test_verify_sha256_mismatch_removes_file(tmp_path: Path) -> None:
    fp = tmp_path / "x"
    fp.write_bytes(b"different content")
    with pytest.raises(UpdaterError, match="SHA-256 mismatch"):
        updater._verify_sha256(fp, "0" * 64)
    assert not fp.exists()


def test_cosign_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)
    with pytest.raises(UpdaterError, match="cosign not on PATH"):
        updater._verify_cosign_bundle(
            tmp_path / "asset", tmp_path / "bundle",
            identity_regex="x", oidc_issuer="y",
        )


# ---------------------------------------------------------------------------
# Manifest round-trip via stubbed urlopen
# ---------------------------------------------------------------------------


def test_fetch_manifest_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({
        "version": "1.3.0",
        "notes_url": "https://example.invalid/release/1.3.0",
        "assets": [
            {
                "platform": "linux",
                "url": "https://example.invalid/x.tar.gz",
                "sha256": "a" * 64,
                "cosign_bundle_url": "https://example.invalid/x.bundle",
            },
        ],
    }).encode("utf-8")

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self, n: int = -1) -> bytes:
            return payload

    monkeypatch.setattr(
        updater.urllib.request, "urlopen", lambda *a, **kw: _Resp(),
    )
    manifest = updater._fetch_manifest("https://example.invalid/manifest.json")
    assert manifest.version == "1.3.0"
    assert manifest.assets[0].platform == "linux"
