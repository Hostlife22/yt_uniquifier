"""AppState — signal emission, recents dedup/cap, history persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.gui.state import HISTORY_CAP, RECENTS_CAP, AppState, HistoryEntry


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppState:
    """AppState whose persistence paths are inside tmp_path."""
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        "yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "history.json",
    )
    return AppState()


def test_set_input_path_emits_signal(isolated_state: AppState) -> None:
    received: list[Path | None] = []
    isolated_state.input_path_changed.connect(received.append)
    isolated_state.set_input_path(Path("/tmp/foo.mp4"))
    assert received == [Path("/tmp/foo.mp4")]


def test_set_input_path_pushes_recent(isolated_state: AppState) -> None:
    isolated_state.set_input_path(Path("/tmp/a.mp4"))
    isolated_state.set_input_path(Path("/tmp/b.mp4"))
    # AppState normalises via ``str(Path(...))`` which produces
    # backslashes on Windows. Match the platform-native form rather
    # than hard-coding POSIX slashes.
    assert isolated_state.recents == [
        str(Path("/tmp/b.mp4")),
        str(Path("/tmp/a.mp4")),
    ]


def test_recents_dedup(isolated_state: AppState) -> None:
    isolated_state.push_recent("/tmp/a.mp4")
    isolated_state.push_recent("/tmp/b.mp4")
    isolated_state.push_recent("/tmp/a.mp4")
    assert isolated_state.recents == ["/tmp/a.mp4", "/tmp/b.mp4"]


def test_recents_cap(isolated_state: AppState) -> None:
    for i in range(RECENTS_CAP + 5):
        isolated_state.push_recent(f"/tmp/{i}.mp4")
    assert len(isolated_state.recents) == RECENTS_CAP


def test_history_persistence_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("yt_uniquifier.gui.state.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yt_uniquifier.gui.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        "yt_uniquifier.gui.state.HISTORY_PATH", tmp_path / "history.json",
    )
    s1 = AppState()
    entry = HistoryEntry(
        timestamp="2026-05-29T12:00:00",
        source_path="/tmp/in.mp4",
        profile_name="cid_aware",
        encoder_name="libx264",
        output_path="/tmp/out.mp4",
        qa_html_path="/tmp/out.mp4.qa.html",
        plan_hash="abc123",
        status="done",
    )
    s1.push_history(entry)
    s1.save()

    s2 = AppState()  # fresh instance — should load from disk
    assert len(s2.history) == 1
    assert s2.history[0].plan_hash == "abc123"


def test_history_cap(isolated_state: AppState) -> None:
    for i in range(HISTORY_CAP + 5):
        isolated_state.push_history(HistoryEntry(
            timestamp=f"2026-05-{i:02d}",
            source_path=f"/tmp/{i}.mp4",
            profile_name="cid_aware",
            encoder_name="libx264",
            output_path=f"/tmp/o{i}.mp4",
            qa_html_path=None,
            plan_hash=f"hash{i:04d}",
            status="done",
        ))
    assert len(isolated_state.history) == HISTORY_CAP
    # Most recent should be the last pushed.
    assert isolated_state.history[0].plan_hash == f"hash{HISTORY_CAP + 4:04d}"


def test_theme_setter_emits_signal(isolated_state: AppState) -> None:
    received: list[str] = []
    isolated_state.theme_changed.connect(received.append)
    isolated_state.set_theme("light")
    assert received == ["light"]
