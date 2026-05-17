"""Unit tests for core/probe.py using fixture ffprobe JSON (no real ffmpeg)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import probe as probe_mod
from yt_uniquifier.core.errors import ProbeError


def _ffprobe_json(
    *,
    video: list[dict[str, Any]] | None = None,
    audio: list[dict[str, Any]] | None = None,
    subtitle: list[dict[str, Any]] | None = None,
    chapters: list[dict[str, Any]] | None = None,
    duration: str = "10.0",
    size: str = "1000000",
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
) -> dict[str, Any]:
    return {
        "streams": [
            *(video or []),
            *(audio or []),
            *(subtitle or []),
        ],
        "chapters": chapters or [],
        "format": {
            "duration": duration,
            "size": size,
            "format_name": format_name,
        },
    }


def _mock_ffprobe(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(probe_mod, "ffprobe_bin", lambda: "/usr/bin/ffprobe")

    class _Proc:
        stdout = json.dumps(payload)
        stderr = ""
        returncode = 0

    def fake_run(*_args: Any, **_kwargs: Any) -> _Proc:
        return _Proc()

    monkeypatch.setattr(probe_mod.subprocess, "run", fake_run)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ProbeError, match="does not exist"):
        probe_mod.probe(tmp_path / "nonexistent.mp4")


def test_parse_basic_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "fake.mp4"
    src.touch()
    payload = _ffprobe_json(
        video=[
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "24000/1001",
                "duration": "9.99",
                "pix_fmt": "yuv420p",
                "bit_rate": "5000000",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "color_space": "bt709",
                "color_range": "tv",
                "bits_per_raw_sample": "8",
                "disposition": {"default": 1},
            }
        ],
        audio=[
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "bit_rate": "256000",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            }
        ],
    )
    _mock_ffprobe(monkeypatch, payload)
    meta = probe_mod.probe(src)

    assert meta.container == "mp4"
    assert len(meta.video) == 1
    v = meta.video[0]
    assert v.codec == "h264"
    assert v.width == 1920
    assert v.height == 1080
    assert v.fps == pytest.approx(23.976, rel=1e-3)
    assert v.pix_fmt == "yuv420p"
    assert v.color.is_hdr is False
    assert v.color.transfer == "bt709"
    assert v.color.bit_depth == 8
    assert v.is_default is True

    assert len(meta.audio) == 1
    a = meta.audio[0]
    assert a.language == "eng"
    assert a.channels == 2


def test_detect_hdr_pq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "hdr.mp4"
    src.touch()
    payload = _ffprobe_json(
        video=[
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "r_frame_rate": "24/1",
                "duration": "10.0",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "bits_per_raw_sample": "10",
            }
        ],
    )
    _mock_ffprobe(monkeypatch, payload)
    meta = probe_mod.probe(src)
    assert meta.video[0].color.is_hdr is True
    assert meta.video[0].color.transfer == "smpte2084"
    assert meta.video[0].color.bit_depth == 10


def test_detect_hdr_hlg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "hlg.mp4"
    src.touch()
    payload = _ffprobe_json(
        video=[
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "50/1",
                "duration": "10.0",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            }
        ],
    )
    _mock_ffprobe(monkeypatch, payload)
    meta = probe_mod.probe(src)
    assert meta.video[0].color.is_hdr is True
    assert meta.video[0].color.transfer == "arib-std-b67"


def test_image_based_subtitle_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "subs.mkv"
    src.touch()
    payload = _ffprobe_json(
        video=[{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1, "height": 1,
                "r_frame_rate": "24/1", "duration": "1", "pix_fmt": "yuv420p"}],
        subtitle=[
            {"index": 2, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
            {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
             "tags": {"language": "eng"}},
        ],
        format_name="matroska,webm",
    )
    _mock_ffprobe(monkeypatch, payload)
    meta = probe_mod.probe(src)
    assert meta.container == "mkv"
    pgs, srt = meta.subtitle
    assert pgs.is_image_based is True
    assert srt.is_image_based is False
    assert srt.language == "eng"


def test_chapters_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "with_chapters.mp4"
    src.touch()
    payload = _ffprobe_json(
        video=[{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1, "height": 1,
                "r_frame_rate": "24/1", "duration": "1", "pix_fmt": "yuv420p"}],
        chapters=[
            {"start_time": "0.0", "end_time": "120.5", "tags": {"title": "Intro"}},
            {"start_time": "120.5", "end_time": "240.0"},
        ],
    )
    _mock_ffprobe(monkeypatch, payload)
    meta = probe_mod.probe(src)
    assert len(meta.chapters) == 2
    assert meta.chapters[0].title == "Intro"
    assert meta.chapters[1].title is None
    assert meta.chapters[1].end_sec == pytest.approx(240.0)


def test_ffprobe_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "bad.mp4"
    src.touch()
    monkeypatch.setattr(probe_mod, "ffprobe_bin", lambda: "/usr/bin/ffprobe")

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.CalledProcessError(1, "ffprobe", stderr="boom")

    monkeypatch.setattr(probe_mod.subprocess, "run", fail)
    with pytest.raises(ProbeError, match="ffprobe failed"):
        probe_mod.probe(src)


def test_invalid_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "garbage.mp4"
    src.touch()
    monkeypatch.setattr(probe_mod, "ffprobe_bin", lambda: "/usr/bin/ffprobe")

    class _Proc:
        stdout = "{not json"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(ProbeError, match="invalid JSON"):
        probe_mod.probe(src)
