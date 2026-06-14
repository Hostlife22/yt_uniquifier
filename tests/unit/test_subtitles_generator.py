"""Unit tests for the whisper-cpp SRT generator (v0.9.0 R2 / F14).

Mocks the ffmpeg + whisper-cpp subprocesses so no binary is required;
the integration test in ``tests/integration/test_subtitles_real.py``
covers the live path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core import subtitles as subs
from yt_uniquifier.core.subtitles import (
    SubtitleGenerationError,
    find_default_model,
    generate_srt,
    is_subtitle_extension_supported,
)
from yt_uniquifier.core.transforms import _whisper_probe


@pytest.fixture(autouse=True)
def _reset_probe_override() -> Any:
    """Ensure the test-only probe override is cleared between tests."""
    _whisper_probe.set_capability_for_tests(None)
    yield
    _whisper_probe.set_capability_for_tests(None)


def _force_capability(*, srt_generator: str | None) -> None:
    _whisper_probe.set_capability_for_tests(_whisper_probe.WhisperCapability(
        burn_in_filter=True,
        srt_generator=srt_generator,
        ffmpeg_native_whisper=False,
    ))


def _fake_run_success(srt_body: str) -> Any:
    """Build a fake ``subprocess.run`` that emulates ffmpeg + whisper-cpp.

    Both subprocesses are invoked by ``generate_srt``; we differentiate
    by the binary name (``ffmpeg`` vs the configured srt_generator).
    """

    def fake(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        argv0 = cmd[0]
        if argv0.endswith("ffmpeg") or "ffmpeg" in argv0:
            # ffmpeg writes the requested wav target with placeholder bytes.
            wav = Path(cmd[-1])
            wav.write_bytes(b"\x00\x00")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        # whisper-cpp: produce <out_stem>.srt
        # Locate ``-of`` argument.
        of_idx = cmd.index("-of")
        out_stem = Path(cmd[of_idx + 1])
        out_stem.with_suffix(".srt").write_text(srt_body, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return fake


def test_generate_srt_raises_when_no_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator=None)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"x")
    with pytest.raises(SubtitleGenerationError, match="no whisper backend"):
        generate_srt(src, model)


def test_generate_srt_raises_when_model_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator="/usr/bin/whisper-cpp")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    with pytest.raises(SubtitleGenerationError, match="model not found"):
        generate_srt(src, tmp_path / "missing-model.bin")


def test_generate_srt_writes_dest_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator="/usr/bin/whisper-cpp")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"abc")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m")
    monkeypatch.setattr(subs.subprocess, "run", _fake_run_success(
        "1\n00:00:00,000 --> 00:00:01,000\nhi\n",
    ))
    # Override ffmpeg_bin to avoid PATH lookup.
    monkeypatch.setattr(subs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")

    cache_dir = tmp_path / "cache"
    dest = tmp_path / "out.srt"
    result = generate_srt(src, model, dest=dest, cache_dir=cache_dir)
    assert result.path == dest.resolve()
    assert result.from_cache is False
    assert dest.read_text(encoding="utf-8").startswith("1\n")
    # Cache marker is keyed by (size, mtime_ns, model name, lang).
    assert any(cache_dir.glob("*.srt"))


def test_generate_srt_serves_from_cache_on_unchanged_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator="/usr/bin/whisper-cpp")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"abc")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m")
    monkeypatch.setattr(subs.subprocess, "run", _fake_run_success("body1\n"))
    monkeypatch.setattr(subs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")

    cache_dir = tmp_path / "cache"
    dest = tmp_path / "out.srt"
    first = generate_srt(src, model, dest=dest, cache_dir=cache_dir)
    assert not first.from_cache

    # Make a fresh run() that would error if invoked, to prove the
    # second call truly served from cache without subprocesses.
    def boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("subprocess should not run on cache hit")

    monkeypatch.setattr(subs.subprocess, "run", boom)
    second = generate_srt(src, model, dest=dest, cache_dir=cache_dir)
    assert second.from_cache is True


def test_generate_srt_force_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator="/usr/bin/whisper-cpp")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"abc")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m")
    monkeypatch.setattr(subs.subprocess, "run", _fake_run_success("first\n"))
    monkeypatch.setattr(subs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    cache_dir = tmp_path / "cache"
    dest = tmp_path / "out.srt"
    generate_srt(src, model, dest=dest, cache_dir=cache_dir)

    monkeypatch.setattr(subs.subprocess, "run", _fake_run_success("second\n"))
    result = generate_srt(src, model, dest=dest, cache_dir=cache_dir, force=True)
    assert result.from_cache is False
    assert "second" in dest.read_text(encoding="utf-8")


def test_generate_srt_propagates_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_capability(srt_generator="/usr/bin/whisper-cpp")
    src = tmp_path / "in.mp4"
    src.write_bytes(b"abc")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m")

    def fail(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        if "ffmpeg" in cmd[0]:
            Path(cmd[-1]).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 2, b"", b"whisper boom")

    monkeypatch.setattr(subs.subprocess, "run", fail)
    monkeypatch.setattr(subs, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with pytest.raises(SubtitleGenerationError, match="whisper-cpp failed"):
        generate_srt(src, model, dest=tmp_path / "x.srt",
                     cache_dir=tmp_path / "c")


# ---------------------------------------------------------------------------
# Auxiliary helpers
# ---------------------------------------------------------------------------


def test_is_subtitle_extension_supported() -> None:
    for ext in (".srt", ".ass", ".SSA", ".sbv", ".vtt"):
        assert is_subtitle_extension_supported(Path(f"x{ext}"))
    assert not is_subtitle_extension_supported(Path("x.txt"))


def test_find_default_model_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point the search at an empty dir; the home-relative defaults
    # should still resolve to None on a clean test host.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert find_default_model() is None


def test_find_default_model_prefers_base_over_tiny(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "ggml-tiny.bin").write_bytes(b"t")
    (tmp_path / "models" / "ggml-base.bin").write_bytes(b"b")
    chosen = find_default_model()
    assert chosen is not None
    assert chosen.name == "ggml-base.bin"
