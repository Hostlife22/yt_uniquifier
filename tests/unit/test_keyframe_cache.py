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


def test_keyframe_cache_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    """v1.0.1 Task 1: _save_keyframe_cache must go through a {pid}.{random}
    tmp file and end with ``os.replace`` — never a direct ``write_text``.

    We patch ``os.replace`` to capture the tmp name and assert that:
      * it contains the current PID,
      * it contains a 4-byte hex token (8 chars),
      * the final destination is the canonical cache path.
    """
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    cache_path = seg_mod._keyframe_cache_path(src)

    captured: dict[str, Any] = {}
    real_replace = seg_mod.os.replace

    def fake_replace(src_path: str, dst_path: str) -> None:
        captured["src"] = str(src_path)
        captured["dst"] = str(dst_path)
        real_replace(src_path, dst_path)

    monkeypatch.setattr(seg_mod.os, "replace", fake_replace)
    seg_mod._save_keyframe_cache(src, [0.0, 1.5, 3.0])

    assert captured["dst"] == str(cache_path)
    tmp_name = Path(captured["src"]).name
    pid = str(seg_mod.os.getpid())
    assert pid in tmp_name, f"tmp name {tmp_name!r} missing pid {pid}"
    # token_hex(4) == 8 hex chars. Look for the .{pid}.{8-hex}. fragment.
    import re
    assert re.search(rf"\.{pid}\.[0-9a-f]{{8}}\.json\.tmp$", tmp_name), (
        f"tmp name {tmp_name!r} does not match expected pid+random pattern"
    )
    assert cache_path.exists()


def test_keyframe_cache_retries_transient_replace_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    """A short-lived Windows destination lock must not fail cache writes."""
    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")
    cache_path = seg_mod._keyframe_cache_path(src)
    real_replace = seg_mod.os.replace
    calls = 0

    def flaky_replace(src_path: str, dst_path: str) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise PermissionError(13, "destination temporarily locked")
        real_replace(src_path, dst_path)

    monkeypatch.setattr(seg_mod.os, "replace", flaky_replace)
    monkeypatch.setattr(seg_mod.time, "sleep", lambda _seconds: None)

    seg_mod._save_keyframe_cache(src, [0.0, 1.5, 3.0])

    assert calls == 3
    assert cache_path.exists()
    assert not list(cache_path.parent.glob("*.tmp"))


def test_keyframe_cache_concurrent_writers_no_torn_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _redirect_cache: Path,
) -> None:
    """v1.0.1 Task 1: two concurrent _save_keyframe_cache writes on the
    same source must land as one whole payload, never a torn merge.

    We race two threads on the same path; afterwards the final JSON must
    decode cleanly and equal one of the two written keyframe lists in
    full. A torn write (mixed bytes) would either fail to parse or
    contain a value neither thread emitted.
    """
    import threading

    src = tmp_path / "x.mp4"
    src.write_bytes(b"x")

    kfs_a = [float(i) for i in range(200)]
    kfs_b = [float(i) + 0.5 for i in range(200)]
    errors: list[BaseException] = []

    def writer(kfs: list[float]) -> None:
        try:
            for _ in range(10):
                seg_mod._save_keyframe_cache(src, kfs)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=(kfs_a,))
    t2 = threading.Thread(target=writer, args=(kfs_b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"writer raised: {errors}"
    cache_path = seg_mod._keyframe_cache_path(src)
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert raw["keyframes"] in (kfs_a, kfs_b), (
        "final cache content does not match either writer's whole payload "
        "— a torn write slipped through atomic replace"
    )
    # No leftover tmp files in the cache dir.
    leftovers = list(cache_path.parent.glob("*.tmp"))
    assert not leftovers, f"leftover tmp files: {leftovers}"
