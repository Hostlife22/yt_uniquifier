"""Unit tests for core/encoder.py with mocked subprocess + isolated cache."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.core import encoder as enc_mod
from yt_uniquifier.core.errors import EncoderError
from yt_uniquifier.core.models import EncoderCandidate


def _stub_run(rc_by_encoder: dict[str, int]) -> Any:
    """Return a fake subprocess.run that succeeds for whitelisted encoders."""

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Encoder name follows '-c:v' in cmd.
        try:
            i = cmd.index("-c:v")
            enc_name = cmd[i + 1]
        except (ValueError, IndexError):
            enc_name = ""
        rc = rc_by_encoder.get(enc_name, 1)
        stderr = "" if rc == 0 else f"Unknown encoder '{enc_name}'\n"
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=stderr)

    return fake_run


@pytest.fixture
def stub_ffmpeg_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enc_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        enc_mod.subprocess,
        "check_output",
        lambda *a, **k: "ffmpeg version 7.0 fake\n",
    )


def test_detect_picks_only_working(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    monkeypatch.setattr(
        enc_mod.subprocess,
        "run",
        _stub_run({"libx264": 0, "libx265": 0, "h264_videotoolbox": 0}),
    )
    cands = enc_mod.detect_encoders(force=True)
    works = {c.name: c.works for c in cands}
    assert works["libx264"] is True
    assert works["libx265"] is True
    assert works["h264_videotoolbox"] is True
    assert works["h264_nvenc"] is False
    assert works["h264_qsv"] is False
    assert isolated_cache.exists()


def test_detect_caches_result(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    call_counter = MagicMock(wraps=_stub_run({"libx264": 0, "libx265": 0}))
    monkeypatch.setattr(enc_mod.subprocess, "run", call_counter)

    enc_mod.detect_encoders(force=True)
    first_calls = call_counter.call_count
    assert first_calls > 0

    enc_mod.detect_encoders(force=False)
    # Cache hit: no extra subprocess.run for encoder probes.
    assert call_counter.call_count == first_calls


def test_force_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    counter = MagicMock(wraps=_stub_run({"libx264": 0, "libx265": 0}))
    monkeypatch.setattr(enc_mod.subprocess, "run", counter)
    enc_mod.detect_encoders(force=True)
    before_forced_repeat = counter.call_count
    enc_mod.detect_encoders(force=True)
    # Assert the work caused by this call, not the process-global total: GUI
    # tests may have a background discovery worker completing concurrently.
    assert counter.call_count - before_forced_repeat == len(enc_mod._CANDIDATES)


def test_cache_invalidated_on_different_ffmpeg_version(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    monkeypatch.setattr(enc_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        enc_mod.subprocess,
        "run",
        _stub_run({"libx264": 0, "libx265": 0}),
    )

    # First pass with version "A".
    monkeypatch.setattr(enc_mod.subprocess, "check_output", lambda *a, **k: "ffmpeg A\n")
    enc_mod.detect_encoders(force=True)

    # Second pass: cache exists but ffmpeg version changed -> should not be reused.
    monkeypatch.setattr(enc_mod.subprocess, "check_output", lambda *a, **k: "ffmpeg B\n")
    cached = enc_mod._load_cache("different-key")
    assert cached is None


def test_failed_probe_records_error(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    monkeypatch.setattr(enc_mod.subprocess, "run", _stub_run({}))
    cands = enc_mod.detect_encoders(force=True)
    failed = [c for c in cands if not c.works]
    assert all(c.error for c in failed)


def test_pick_encoder_prefers_explicit() -> None:
    cands = [
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True),
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]
    assert (
        enc_mod.pick_encoder(cands, prefer=["libx264"], codec="h264").name == "libx264"
    )


def test_pick_encoder_fallback_to_libx264() -> None:
    cands = [
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=False, error="x"),
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]
    assert enc_mod.pick_encoder(cands, codec="h264").name == "libx264"


def test_pick_encoder_no_working_raises() -> None:
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=False, error="x"),
    ]
    with pytest.raises(EncoderError):
        enc_mod.pick_encoder(cands, codec="h264")


def test_pick_encoder_picks_priority_when_multiple(
    isolated_cache: Path,
) -> None:
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True),
    ]
    # Without prefer: canonical order has nvenc first.
    assert enc_mod.pick_encoder(cands, codec="h264").name == "h264_nvenc"


def test_cache_file_atomic_write(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    monkeypatch.setattr(enc_mod.subprocess, "run", _stub_run({"libx264": 0}))
    enc_mod.detect_encoders(force=True)
    payload = json.loads(isolated_cache.read_text())
    assert payload["schema_version"] == 1
    assert payload["candidates"][0]["name"] == "h264_nvenc"  # canonical order preserved


def test_plan_capability_probe_caches_exact_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = MagicMock()
    plan.source.video = [MagicMock(width=1920, height=1080)]
    command = [
        "/usr/bin/ffmpeg", "-f", "lavfi", "-i",
        "testsrc2=s=1920x1080:r=24:d=0.25", "-f", "null", "-",
    ]
    run = MagicMock(
        return_value=subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(enc_mod, "_ffmpeg_version_hash", lambda: "version-device")
    monkeypatch.setattr(enc_mod.subprocess, "run", run)
    monkeypatch.setattr(
        "yt_uniquifier.core.pipeline.build_encoder_capability_probe",
        lambda _plan: command,
    )
    monkeypatch.setattr(
        "yt_uniquifier.core.pipeline._segment_pix_fmt", lambda _plan: "yuv420p",
    )
    enc_mod._CAPABILITY_CACHE.clear()

    first = enc_mod.probe_encoder_for_plan(plan)
    second = enc_mod.probe_encoder_for_plan(plan)

    assert first.supported is True
    assert second == first
    assert run.call_count == 1


def test_plan_capability_probe_does_not_cache_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = MagicMock()
    plan.source.video = [MagicMock(width=1920, height=1080)]
    command = [
        "/usr/bin/ffmpeg", "-f", "lavfi", "-i",
        "testsrc2=s=1920x1080:r=24:d=0.25", "-f", "null", "-",
    ]
    run = MagicMock(side_effect=[
        subprocess.CompletedProcess(
            command, 1, stdout="", stderr="hardware session unavailable",
        ),
        subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    ])
    monkeypatch.setattr(enc_mod, "_ffmpeg_version_hash", lambda: "version-device")
    monkeypatch.setattr(enc_mod.subprocess, "run", run)
    monkeypatch.setattr(
        "yt_uniquifier.core.pipeline.build_encoder_capability_probe",
        lambda _plan: command,
    )
    monkeypatch.setattr(
        "yt_uniquifier.core.pipeline._segment_pix_fmt", lambda _plan: "yuv420p",
    )
    enc_mod._CAPABILITY_CACHE.clear()

    first = enc_mod.probe_encoder_for_plan(plan)
    second = enc_mod.probe_encoder_for_plan(plan)

    assert first.supported is False
    assert second.supported is True
    assert run.call_count == 2
