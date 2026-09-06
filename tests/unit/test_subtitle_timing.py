from pathlib import Path

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.subtitle_timing import retime_canonical_srt


@pytest.mark.parametrize("rate, start, end", [
    (0.5, "00:00:00,200", "00:00:02,400"),
    (0.98, "00:00:00,102", "00:00:01,224"),
    (1.02, "00:00:00,098", "00:00:01,176"),
    (2.0, "00:00:00,050", "00:00:00,600"),
])
def test_srt_retime_preserves_payload_and_positioning(
    tmp_path: Path, rate: float, start: str, end: str,
) -> None:
    source, output = tmp_path / "source.srt", tmp_path / "output.srt"
    payload = "Разрешённый текст\n00:00:00,100 --> 00:00:01,200\n"
    source.write_text(
        "1\n00:00:00,100 --> 00:00:01,200 X1:20\n" + payload + "\n",
        encoding="utf-8",
    )
    retime_canonical_srt(source, output, rate)
    assert output.read_text(encoding="utf-8") == f"1\n{start} --> {end} X1:20\n{payload}\n"


@pytest.mark.parametrize("content", ["1\n", "not-an-index\n", "1\nbad clock\n"])
def test_invalid_canonical_srt_fails_closed(tmp_path: Path, content: str) -> None:
    source = tmp_path / "source.srt"
    source.write_text(content)
    with pytest.raises(PipelineError):
        retime_canonical_srt(source, tmp_path / "output.srt", 2)
