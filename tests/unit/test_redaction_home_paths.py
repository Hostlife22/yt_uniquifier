from pathlib import PurePosixPath, PureWindowsPath

import pytest

from yt_uniquifier.core import redaction


@pytest.mark.parametrize("home, value", [
    ("/Users/alice", "/Users/alice/Private Projects/Client Name/x.mp4"),
    (r"C:\Users\alice", r"C:\Users\alice\Private Projects\Client Name\x.mp4"),
    (r"C:\Users\alice", r"\\server\Private Projects\Client Name\x.mp4"),
])
def test_public_path_fields_hide_home_and_space_containing_directories(
    monkeypatch: pytest.MonkeyPatch, home: str, value: str,
) -> None:
    home_path = PureWindowsPath(home) if "\\" in home else PurePosixPath(home)
    monkeypatch.setattr(redaction.Path, "home", lambda: home_path)
    assert redaction.redact_path(value, all_absolute=True) == "<PATH>/x.mp4"
    assert redaction.redact_text(value, all_absolute_paths=True) == "<PATH>/x.mp4"
    result = redaction.redact_mapping({"payload": {"input": value}}, all_absolute_paths=True)
    assert result == {"payload": {"input": "<PATH>/x.mp4"}}


def test_private_home_only_redaction_keeps_its_existing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redaction.Path, "home", lambda: PureWindowsPath(r"C:\Users\alice"))
    assert redaction.redact_path(r"C:\Users\alice\Clips\x.mp4") == r"<HOME>\Clips\x.mp4"
