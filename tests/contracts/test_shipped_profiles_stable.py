"""Lock the dumped form of every shipped profile YAML.

These goldens serve a different purpose than the schema snapshots:
schemas catch type / field changes; YAML dumps catch *parameter*
changes. If you bump the noise strength in ``soft.yaml``, the
schema test passes but this one fails, prompting a CHANGELOG entry
even when the contract surface itself did not move.

Profiles are loaded through ``load_profile`` (which enforces
``extra=forbid``), then dumped via ``Profile.model_dump(mode='json')``
so ``Path``/``Enum``/``datetime`` collapse to JSON primitives and
the diff stays platform-independent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts._snapshot import snapshot
from yt_uniquifier.core import Profile
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).resolve().parents[2] / "src" / "yt_uniquifier" / "profiles"

SHIPPED = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


@pytest.mark.parametrize("profile_name", SHIPPED)
def test_shipped_profile_dump_is_stable(profile_name: str) -> None:
    profile = load_profile(PROFILES_DIR / f"{profile_name}.yaml")
    assert isinstance(profile, Profile)
    snapshot(f"profiles/{profile_name}.json", profile.model_dump(mode="json"))


def test_shipped_profiles_set_is_complete() -> None:
    """If a profile is added or removed, force the maintainer to
    notice — adding one is MINOR (advertise it in CHANGELOG +
    docs/profiles.md), removing one is MAJOR (RFC required)."""
    snapshot("shipped_profiles.json", SHIPPED)
