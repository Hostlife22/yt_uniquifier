"""Inventory the actual release bundle, bound to its archived artifact hash.

File coverage is not dependency/license completeness: embedded PYZ/static-library
dependencies without metadata remain opaque. No build-environment packages are
misrepresented as shipped components. Symlinks are recorded, never traversed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from email.parser import Parser
from pathlib import Path
from typing import Any
from urllib.parse import quote


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path, artifact: Path, *, version: str, commit: str) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir() or not artifact.is_file():
        raise ValueError("bundle directory and final archive are required")
    components: list[dict[str, Any]] = []
    packages: dict[tuple[str, str], dict[str, Any]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        links = [name for name in dirs if (Path(directory) / name).is_symlink()]
        dirs[:] = sorted(name for name in dirs if name not in links)
        for name in sorted(files + links):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            component: dict[str, Any] = {
                "type": "file", "bom-ref": f"file:{relative}", "name": relative,
            }
            if path.is_symlink():
                component["properties"] = [{
                    "name": "yt-uniquifier:symlink-target", "value": os.readlink(path),
                }]
            elif path.is_file():
                component["hashes"] = [{"alg": "SHA-256", "content": sha256(path)}]
                if name == "METADATA" and path.parent.name.endswith(".dist-info"):
                    metadata = Parser().parsestr(path.read_text(encoding="utf-8"))
                    package, package_version = metadata.get("Name"), metadata.get("Version")
                    if package and package_version:
                        key = package, package_version
                        packages.setdefault(key, {
                            "type": "library", "bom-ref": f"python:{package}@{package_version}",
                            "name": package, "version": package_version,
                            "purl": f"pkg:pypi/{quote(package.lower().replace('_', '-'))}"
                            f"@{quote(package_version)}",
                            "properties": [{
                                "name": "yt-uniquifier:bundled-metadata", "value": relative,
                            }],
                        })
            else:
                raise ValueError(f"unsupported bundle entry: {relative}")
            components.append(component)
    if not components:
        raise ValueError("bundle is empty")
    components.extend(packages[key] for key in sorted(packages))
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {
            "component": {
                "type": "application", "bom-ref": "release-artifact",
                "name": artifact.name, "version": version,
                "hashes": [{"alg": "SHA-256", "content": sha256(artifact)}],
            },
            "properties": [
                {"name": "yt-uniquifier:commit", "value": commit},
                {"name": "yt-uniquifier:inventory-scope", "value": (
                    "All regular files and symlinks in actual bundle; Python packages from "
                    "shipped METADATA only. Opaque embedded/static dependencies, external "
                    "system libraries and complete license attribution NOT VERIFIED."
                )},
            ],
        },
        "components": components,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root, args.artifact, version=args.version, commit=args.commit)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(f"Inventoried {len(result['components'])} components in {args.output.name}")


def verify_release_inventory(directory: Path, commit: str) -> None:
    documents = sorted(directory.glob("yt-uniq-gui-*.sbom.cdx.json"))
    if len(documents) != 4:
        raise ValueError("expected all four platform/AppImage inventories")
    for path in documents:
        document = json.loads(path.read_text(encoding="utf-8"))
        metadata = document["metadata"]
        properties = {item["name"]: item["value"] for item in metadata["properties"]}
        if properties["yt-uniquifier:commit"] != commit:
            raise ValueError("inventory source commit mismatch")
        artifact_name = metadata["component"]["name"]
        if Path(artifact_name).name != artifact_name:
            raise ValueError("unsafe artifact name")
        expected_hash = metadata["component"]["hashes"][0]
        if expected_hash != {"alg": "SHA-256", "content": sha256(directory / artifact_name)}:
            raise ValueError("inventory/archive hash mismatch")
        if not document.get("components"):
            raise ValueError("empty inventory")


if __name__ == "__main__":
    main()
