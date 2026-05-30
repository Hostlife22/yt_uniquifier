"""Test YAML profile loading + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.profile_loader import ProfileLoadError, load_profile

REPO_PROFILES = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@pytest.mark.parametrize(
    "name",
    [
        "soft", "medium", "aggressive",
        "cid_aware", "cid_aggressive",
        "medium_hdr", "cid_aware_hdr_to_sdr",
    ],
)
def test_shipped_profiles_load(name: str) -> None:
    p = load_profile(REPO_PROFILES / f"{name}.yaml")
    assert p.name == name
    # HDR profiles target hevc; SDR profiles target h264.
    assert p.target_codec in {"h264", "hevc"}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileLoadError, match="not found"):
        load_profile(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("transforms: [oops\n")
    with pytest.raises(ProfileLoadError, match="YAML parse error"):
        load_profile(bad)


def test_unknown_field_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "extra.yaml"
    bad.write_text("name: x\nsomething_else: true\n")
    with pytest.raises(ProfileLoadError):
        load_profile(bad)
