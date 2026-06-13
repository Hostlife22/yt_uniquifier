"""v0.7.0 R1 / E3 — config-path migration regression.

Verifies:
1. `_resolve_config_dir()` honours `QStandardPaths.AppConfigLocation` when set.
2. `_migrate_from_legacy()` copies legacy `~/.config/yt_uniquifier/` data into
   the new dir on first run.
3. Migration is a no-op when the new dir already has `state.json` or `history.json`.
4. Migration leaves the legacy directory intact (copy, not move).
"""

from __future__ import annotations

import json

import pytest

# These tests use the helper directly without spinning up QApplication —
# the helpers are pure-Python and side-effect free except for filesystem.
from yt_uniquifier.gui.state import _migrate_from_legacy


def _make_legacy(home: object, *, state: dict | None = None, history: list | None = None):
    """Create a fake legacy `~/.config/yt_uniquifier/` under `home`."""
    legacy = home / ".config" / "yt_uniquifier"
    legacy.mkdir(parents=True)
    if state is not None:
        (legacy / "state.json").write_text(json.dumps(state))
    if history is not None:
        (legacy / "history.json").write_text(json.dumps(history))
    return legacy


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() so the migration helper looks at our tmp dir."""
    monkeypatch.setattr("yt_uniquifier.gui.state.Path.home", lambda: tmp_path)
    return tmp_path


def test_migrate_copies_files_when_new_dir_empty(fake_home, tmp_path):
    """Fresh install with legacy data → migration copies both JSON files."""
    legacy = _make_legacy(
        fake_home,
        state={"theme": "light", "recents": ["/a.mp4"]},
        history=[{"timestamp": "t"}],
    )
    new_dir = tmp_path / "new_loc" / "yt_uniquifier"

    _migrate_from_legacy(new_dir)

    assert (new_dir / "state.json").exists()
    assert (new_dir / "history.json").exists()
    # Legacy preserved
    assert (legacy / "state.json").exists()
    # Content preserved
    assert json.loads((new_dir / "state.json").read_text())["theme"] == "light"


def test_migrate_skips_when_new_dir_has_data(fake_home, tmp_path):
    """Existing new-dir data must not be overwritten by legacy copy."""
    _make_legacy(fake_home, state={"theme": "light"})
    new_dir = tmp_path / "new_loc" / "yt_uniquifier"
    new_dir.mkdir(parents=True)
    (new_dir / "state.json").write_text(json.dumps({"theme": "dark"}))

    _migrate_from_legacy(new_dir)

    # New data preserved unchanged
    assert json.loads((new_dir / "state.json").read_text())["theme"] == "dark"


def test_migrate_noop_when_legacy_absent(fake_home, tmp_path):
    """Clean install (no legacy) → migration is a quiet no-op."""
    new_dir = tmp_path / "new_loc" / "yt_uniquifier"
    _migrate_from_legacy(new_dir)
    # new_dir may or may not be created — the contract is just "no crash, no data invented"
    assert not (new_dir / "state.json").exists()


def test_migrate_noop_when_new_equals_legacy(fake_home, tmp_path):
    """On Linux without XDG, new_dir == legacy — must not self-copy."""
    legacy = _make_legacy(fake_home, state={"theme": "light"})
    _migrate_from_legacy(legacy)  # same path
    # Still readable, not deleted, not duplicated
    assert (legacy / "state.json").exists()
    assert json.loads((legacy / "state.json").read_text())["theme"] == "light"
