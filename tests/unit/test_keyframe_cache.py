"""list_keyframes cache hit/miss + invalidation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import segmenter as seg_mod


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "kf_cache"
    monkeypatch.setattr(seg_mod, "KEYFRAME_CACHE_DIR", cache)
    return cache


def _stub_ffprobe(monkeypatch: pytest.MonkeyPatch, frames: list[float]) -> dict[str, int]:
    calls = {"n": 0}

    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls["n"] += 1
        payload = {"frames": [{"pts_time": str(t)} for t in frames]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(seg_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(seg_mod, "ffprobe_bin", lambda: "/usr/bin/ffprobe")
    return calls


def test_first_call_writes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    src = tmp_path / "x.mp4"
    src.write_bytes(b"fake mp4 bytes")
    calls = _stub_ffprobe(monkeypatch, [0.0, 5.0, 10.0])

    out = seg_mod.list_keyframes(src)
    assert out == [0.0, 5.0, 10.0]
    assert calls["n"] == 1
    cache_path = seg_mod._keyframe_cache_path(src)
    assert cache_path.exists()


def test_second_call_is_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    src = tmp_path / "x.mp4"
    src.write_bytes(b"fake mp4 bytes")
    calls = _stub_ffprobe(monkeypatch, [0.0, 5.0])

    seg_mod.list_keyframes(src)
    seg_mod.list_keyframes(src)
    # ffprobe called only once.
    assert calls["n"] == 1


def test_force_bypasses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    src = tmp_path / "x.mp4"
    src.write_bytes(b"fake mp4 bytes")
    calls = _stub_ffprobe(monkeypatch, [0.0])

    seg_mod.list_keyframes(src)
    seg_mod.list_keyframes(src, force=True)
    assert calls["n"] == 2


def test_different_file_different_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    a = tmp_path / "a.mp4"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"BBB")
    calls = _stub_ffprobe(monkeypatch, [0.0])

    seg_mod.list_keyframes(a)
    seg_mod.list_keyframes(b)
    # Different MD5 → different cache file → both probes happened.
    assert calls["n"] == 2


def test_stale_cache_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    cache_path = seg_mod._keyframe_cache_path(src)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Write a cache entry that's 60 days old.
    cache_path.write_text(json.dumps({
        "schema_version": 1, "written_at": 0,  # epoch — way past TTL
        "keyframes": [0.0, 5.0],
    }))
    calls = _stub_ffprobe(monkeypatch, [0.0, 5.0, 10.0])

    out = seg_mod.list_keyframes(src)
    assert out == [0.0, 5.0, 10.0]
    assert calls["n"] == 1


def test_corrupt_cache_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    cache_path = seg_mod._keyframe_cache_path(src)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{ not json")
    calls = _stub_ffprobe(monkeypatch, [0.0])

    seg_mod.list_keyframes(src)
    assert calls["n"] == 1
