"""Real-FFmpeg validation for source/output seam diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from tools import seam_test
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize(("inject_defect", "expected"), [(False, 0), (True, 1)])
def test_seam_tool_distinguishes_clean_encode_from_boundary_defect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inject_defect: bool,
    expected: int,
) -> None:
    source = tmp_path / "source.mkv"
    output = tmp_path / "output.mkv"
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=6",
            "-c:v", "ffv1", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    video_filter = (
        "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,2.75,3.25)'"
        if inject_defect
        else "null"
    )
    subprocess.run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vf", video_filter, "-c:v", "ffv1", str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    (work / "state.json").write_text(
        json.dumps({
            "segments": [
                {"idx": 0, "start_sec": 0.0, "end_sec": 3.0},
                {"idx": 1, "start_sec": 3.0, "end_sec": 6.0},
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seam_test.py", str(output), "--source", str(source),
            "--work-dir", str(work), "--frames", "12", "--search-frames", "1",
            "--threshold", "0.01",
        ],
    )

    assert seam_test.main() == expected
