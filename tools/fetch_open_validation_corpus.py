"""Fetch and verify the pinned open-content validation corpus."""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = REPO / "validation-corpus" / "open-sources.yaml"
DEFAULT_MEDIA = REPO / "validation-corpus" / "media"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_sources(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("source manifest schema_version must be 1")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("source manifest must contain a non-empty sources list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(sources):
        if not isinstance(raw, dict):
            raise ValueError(f"sources[{index}] must be a mapping")
        required = ("id", "filename", "url", "license", "license_url", "sha256")
        if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
            raise ValueError(f"sources[{index}] has missing string fields")
        source_id = raw["id"]
        filename = Path(raw["filename"])
        expected_bytes = raw.get("expected_bytes")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        if filename.name != raw["filename"]:
            raise ValueError(f"unsafe filename for source {source_id}")
        if not isinstance(expected_bytes, int) or expected_bytes <= 0:
            raise ValueError(f"invalid expected_bytes for source {source_id}")
        digest = raw["sha256"]
        parsed_url = urllib.parse.urlsplit(raw["url"])
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid sha256 for source {source_id}")
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError(f"source {source_id} must use an absolute HTTPS URL")
        seen.add(source_id)
        result.append(raw)
    return result


def _verify(path: Path, source: dict[str, Any]) -> None:
    actual_size = path.stat().st_size
    if actual_size != source["expected_bytes"]:
        raise ValueError(
            f"{source['id']}: expected {source['expected_bytes']} bytes, got {actual_size}"
        )
    actual_hash = _sha256(path)
    if actual_hash != source["sha256"]:
        raise ValueError(
            f"{source['id']}: sha256 mismatch: expected {source['sha256']}, "
            f"got {actual_hash}"
        )


def _download(source: dict[str, Any], destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.is_file() and partial.stat().st_size == source["expected_bytes"]:
        _verify(partial, source)
        os.replace(partial, destination)
        return
    expected_bytes = int(source["expected_bytes"])
    if partial.is_file() and partial.stat().st_size > expected_bytes:
        partial.unlink()
    initial_offset = partial.stat().st_size if partial.is_file() else 0
    for offset in (initial_offset, 0):
        if offset == 0 and partial.exists():
            partial.unlink()
        headers = {"User-Agent": "yt-uniquifier-open-corpus/1.5"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(source["url"], headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=60)  # noqa: S310
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{source['id']}: download failed: {exc}") from exc
        try:
            with response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme != "https" or not final_url.netloc:
                    raise ValueError(f"{source['id']}: redirect left HTTPS")
                append = offset > 0 and response.status == 206
                written = offset if append else 0
                mode = "ab" if append else "wb"
                with partial.open(mode) as handle:
                    while chunk := response.read(1024 * 1024):
                        written += len(chunk)
                        if written > expected_bytes:
                            raise ValueError(
                                f"{source['id']}: response exceeds expected byte size"
                            )
                        handle.write(chunk)
            _verify(partial, source)
        except ValueError:
            partial.unlink(missing_ok=True)
            if offset:
                continue
            raise
        os.replace(partial, destination)
        return
    raise RuntimeError(f"{source['id']}: resumed download could not be verified")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    sources = _load_sources(args.sources.resolve())
    requested = set(args.ids)
    known = {source["id"] for source in sources}
    unknown = requested - known
    if unknown:
        parser.error(f"unknown source ids: {', '.join(sorted(unknown))}")
    selected = [source for source in sources if not requested or source["id"] in requested]
    media_dir = args.media_dir.resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    for source in selected:
        destination = media_dir / source["filename"]
        if not destination.is_file():
            if args.verify_only:
                raise FileNotFoundError(f"missing corpus source: {destination}")
            print(f"fetching {source['id']} -> {destination}")
            _download(source, destination)
        _verify(destination, source)
        print(f"verified {source['id']}: {source['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
