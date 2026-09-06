import json
from pathlib import Path

import pytest

from tools.bundle_sbom import inventory, sha256, verify_release_inventory


def test_inventory_binds_actual_files_and_shipped_metadata(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    metadata = root / "example-1.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: example\nVersion: 1.0\n")
    native = root / "QtCore.dll"
    native.write_bytes(b"actual native bytes")
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"archive")
    result = inventory(root, archive, version="1.6.0", commit="a" * 40)
    assert result["metadata"]["component"]["hashes"][0]["content"] == sha256(archive)
    components = {c["name"]: c for c in result["components"]}
    assert set(components) == {"QtCore.dll", "example-1.dist-info/METADATA", "example"}
    assert components["QtCore.dll"]["hashes"][0]["content"] == sha256(native)
    assert components["example"]["version"] == "1.0"
    native.write_bytes(b"replacement")
    changed = inventory(root, archive, version="1.6.0", commit="a" * 40)
    assert changed != result


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret").write_text("not shipped")
    try:
        (root / "link").symlink_to("../private", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    artifact = tmp_path / "release.zip"
    artifact.touch()
    result = inventory(root, artifact, version="1", commit="test")
    assert len(result["components"]) == 1
    assert result["components"][0]["name"] == "link"
    assert "hashes" not in result["components"][0]


def test_empty_bundle_rejected(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    archive = tmp_path / "artifact"
    archive.touch()
    with pytest.raises(ValueError, match="empty"):
        inventory(root, archive, version="1", commit="test")


def test_release_binding_rejects_replaced_archive_or_wrong_commit(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "binary").write_bytes(b"binary")
    for platform in ("Linux", "macOS", "Windows", "AppImage"):
        artifact = tmp_path / f"{platform}.zip"
        artifact.write_bytes(b"archive")
        document = inventory(root, artifact, version="1.6.0", commit="abc")
        (tmp_path / f"yt-uniq-gui-{platform}.sbom.cdx.json").write_text(json.dumps(document))
    verify_release_inventory(tmp_path, "abc")
    with pytest.raises(ValueError, match="commit"):
        verify_release_inventory(tmp_path, "different")
    (tmp_path / "Linux.zip").write_bytes(b"replaced")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_release_inventory(tmp_path, "abc")
