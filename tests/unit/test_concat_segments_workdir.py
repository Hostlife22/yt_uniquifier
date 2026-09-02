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
    args = _args[0]
    assert isinstance(args, list)
    Path(args[-1]).write_bytes(b"muxed")
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
        Path(args[-1]).write_bytes(b"muxed")
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


def test_concat_is_atomic_and_preserves_existing_output_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "final.mp4"
    output.write_bytes(b"previous-good-output")
    segment = tmp_path / "seg.mkv"
    segment.touch()

    def _fail(args: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        Path(args[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, args, stderr="mux failed")

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _fail)

    with pytest.raises(Exception, match="concat failed"):
        segmenter_mod.concat_segments(
            [segment], None, output, [], work_dir=tmp_path / "work",
        )

    assert output.read_bytes() == b"previous-good-output"
    assert not list(tmp_path.glob(".*.part.mp4"))


def test_concat_maps_all_requested_passthrough_audio_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _capture(args: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        Path(args[-1]).write_bytes(b"muxed")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _capture)
    segment = tmp_path / "seg.mkv"
    segment.touch()
    segmenter_mod.concat_segments(
        [segment],
        None,
        tmp_path / "final.mp4",
        [],
        work_dir=tmp_path / "work",
        audio_passthrough_count=5,
    )

    maps = [captured[idx + 1] for idx, value in enumerate(captured) if value == "-map"]
    assert maps == [
        "0:v:0", "0:a:0?", "0:a:1?", "0:a:2?", "0:a:3?",
        "0:a:4?", "0:a:5?", "0:s?",
    ]


def test_concat_maps_media_streams_and_chapters_from_original_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _capture(args: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        Path(args[-1]).write_bytes(b"muxed")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _capture)
    segment = tmp_path / "seg.mkv"
    source = tmp_path / "source.mkv"
    segment.touch()
    source.touch()

    segmenter_mod.concat_segments(
        [segment], None, tmp_path / "final.mp4", [],
        work_dir=tmp_path / "work", media_source=source,
        map_chapters_from=source, audio_passthrough_count=1,
        subtitle_codecs=["subrip"],
    )

    maps = [captured[idx + 1] for idx, value in enumerate(captured) if value == "-map"]
    assert maps == ["0:v:0", "1:a:0?", "1:a:1?", "1:s?"]
    assert captured.count(str(source)) == 1
    assert captured[captured.index("-map_chapters") + 1] == "1"
    assert captured[captured.index("-c:s") + 1] == "mov_text"
