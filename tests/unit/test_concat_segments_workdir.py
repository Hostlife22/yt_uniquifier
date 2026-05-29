"""Regression: concat.txt must live in work_dir, not output.parent.

The earlier implementation placed the transient ffmpeg concat-demuxer
file at ``output.parent / 'concat.txt'``. Two parallel ``yt-uniq batch``
jobs writing to the same output directory raced on that path and
silently swapped each other's contents.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yt_uniquifier.core import segmenter as segmenter_mod


def _stub_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_concat_list_written_in_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(segmenter_mod.subprocess, "run", _stub_run)

    work_dir = tmp_path / "work" / "job_A"
    work_dir.mkdir(parents=True)
    output = tmp_path / "shared_outputs" / "final.mp4"
    output.parent.mkdir(parents=True)

    seg = tmp_path / "seg_0000.mkv"
    seg.touch()

    segmenter_mod.concat_segments(
        [seg], None, output, [], work_dir=work_dir,
    )

    assert (work_dir / "concat.txt").exists(), (
        "concat.txt must be written inside the per-job work_dir"
    )
    assert not (output.parent / "concat.txt").exists(), (
        "concat.txt must NOT be left in the shared output directory "
        "(parallel batch jobs would race on the same path)"
    )


def test_concat_list_isolated_between_parallel_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two jobs sharing output.parent but with distinct work_dirs must
    not see each other's concat.txt."""

    def _capture(args: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _capture)

    shared_out = tmp_path / "shared"
    shared_out.mkdir()
    work_a = tmp_path / "work_a"
    work_b = tmp_path / "work_b"
    work_a.mkdir()
    work_b.mkdir()

    seg_a = tmp_path / "a_0.mkv"
    seg_b = tmp_path / "b_0.mkv"
    seg_a.touch()
    seg_b.touch()

    segmenter_mod.concat_segments(
        [seg_a], None, shared_out / "a.mp4", [], work_dir=work_a,
    )
    segmenter_mod.concat_segments(
        [seg_b], None, shared_out / "b.mp4", [], work_dir=work_b,
    )

    a_concat = (work_a / "concat.txt").read_text()
    b_concat = (work_b / "concat.txt").read_text()
    assert "a_0.mkv" in a_concat
    assert "b_0.mkv" in b_concat
    # Job A's concat list must NOT mention job B's segment.
    assert "b_0.mkv" not in a_concat
    assert "a_0.mkv" not in b_concat
