from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self

import pytest
import yaml

from tools import fetch_open_validation_corpus as fetcher


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://example.invalid/open.mp4"

    def read(self, _size: int) -> bytes:
        payload, self._payload = self._payload, b""
        return payload


def _manifest(filename: str, payload: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sources": [{
            "id": "open-clip",
            "filename": filename,
            "url": "https://example.invalid/open.mp4",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "expected_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }


def test_source_manifest_and_payload_verify(tmp_path: Path) -> None:
    payload = b"pinned open fixture"
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump(_manifest("open.mp4", payload)), encoding="utf-8")
    source = fetcher._load_sources(manifest)[0]
    media = tmp_path / "open.mp4"
    media.write_bytes(payload)

    fetcher._verify(media, source)


def test_source_manifest_rejects_path_escape(tmp_path: Path) -> None:
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(
        yaml.safe_dump(_manifest("../outside.mp4", b"fixture")),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe filename"):
        fetcher._load_sources(manifest)


def test_source_manifest_rejects_non_https_url(tmp_path: Path) -> None:
    payload = _manifest("open.mp4", b"fixture")
    payload["sources"][0]["url"] = "file:///etc/passwd"  # type: ignore[index]
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="absolute HTTPS"):
        fetcher._load_sources(manifest)


def test_source_payload_replacement_fails_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(
        yaml.safe_dump(_manifest("open.mp4", b"expected")),
        encoding="utf-8",
    )
    source = fetcher._load_sources(manifest)[0]
    media = tmp_path / "open.mp4"
    media.write_bytes(b"replaced")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        fetcher._verify(media, source)


def test_complete_partial_is_verified_and_published_without_network(
    tmp_path: Path,
) -> None:
    payload = b"complete resumable fixture"
    source = _manifest("open.mp4", payload)["sources"][0]  # type: ignore[index]
    destination = tmp_path / "open.mp4"
    partial = tmp_path / "open.mp4.part"
    partial.write_bytes(payload)

    fetcher._download(source, destination)  # type: ignore[arg-type]

    assert destination.read_bytes() == payload
    assert not partial.exists()


def test_corrupt_resumed_download_retries_once_from_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"correct"
    source = _manifest("open.mp4", payload)["sources"][0]  # type: ignore[index]
    destination = tmp_path / "open.mp4"
    partial = tmp_path / "open.mp4.part"
    partial.write_bytes(b"x")
    responses = iter([_Response(b"xxxxxx", status=206), _Response(payload)])
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *_a, **_k: next(responses))

    fetcher._download(source, destination)  # type: ignore[arg-type]

    assert destination.read_bytes() == payload
    assert not partial.exists()


def test_oversize_response_stops_before_unbounded_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"expected"
    source = _manifest("open.mp4", payload)["sources"][0]  # type: ignore[index]
    destination = tmp_path / "open.mp4"
    monkeypatch.setattr(
        fetcher.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(payload + b"unexpected"),
    )

    with pytest.raises(ValueError, match="exceeds expected byte size"):
        fetcher._download(source, destination)  # type: ignore[arg-type]

    assert not destination.exists()
    assert not (tmp_path / "open.mp4.part").exists()
