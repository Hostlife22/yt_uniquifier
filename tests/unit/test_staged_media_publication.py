from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import segmenter
from yt_uniquifier.core.errors import PipelineError


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
