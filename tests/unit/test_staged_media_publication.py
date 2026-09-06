from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import segmenter
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Chapter


def test_invalid_staged_media_never_replaces_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "published.mp4"
    output.write_bytes(b"previous-validated-output")
    segment = tmp_path / "segment.mkv"
    segment.write_bytes(b"video")

    def mux(command: Any, *, output: Path, **kwargs: Any) -> None:
        output.write_bytes(b"new-but-invalid")

    def reject(staged: Path) -> None:
        assert staged != output
        assert staged.read_bytes() == b"new-but-invalid"
        raise PipelineError("injected media/peak validation failure")

    monkeypatch.setattr(segmenter, "run_ffmpeg", mux)
    with pytest.raises(PipelineError, match="injected media/peak"):
        segmenter.concat_segments(
            [segment], None, output, [], work_dir=tmp_path / "work",
            audio_source_indices=[], validate_staged=reject,
        )
    assert output.read_bytes() == b"previous-validated-output"
    assert not list(tmp_path.glob(".published.*.part.mp4"))


def test_mux_uses_explicit_chapter_clock_and_disables_priming_shift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    segment = tmp_path / "segment.mkv"
    segment.write_bytes(b"video")

    def mux(command: Any, *, output: Path, **kwargs: Any) -> None:
        commands.append(command.args)
        output.write_bytes(b"muxed")

    monkeypatch.setattr(segmenter, "run_ffmpeg", mux)
    work = tmp_path / "work"
    segmenter.concat_segments(
        [segment], None, tmp_path / "output.mkv", [], work_dir=work,
        audio_source_indices=[], map_chapters_from=tmp_path / "original.mkv",
        chapters=[Chapter(start_sec=0, end_sec=1.5)],
    )
    chapter_file = work / "retimed-chapters.ffmeta"
    assert "START=0\nEND=1500000" in chapter_file.read_text()
    assert str(chapter_file) in commands[0]
    assert str(tmp_path / "original.mkv") not in commands[0]
    index = commands[0].index("-avoid_negative_ts")
    assert commands[0][index + 1] == "disabled"
