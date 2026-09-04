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


def test_detect_keeps_cache_path_captured_at_start(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    """A concurrent config change must not redirect an in-flight cache write."""
    redirected = isolated_cache.with_name("redirected.json")
    base_run = _stub_run({"libx264": 0, "libx265": 0})
    switched = False

    def switch_path_during_probe(
        cmd: list[str], **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal switched
        if not switched:
            switched = True
            monkeypatch.setattr(enc_mod, "CACHE_PATH", redirected)
        return base_run(cmd, **kwargs)

    monkeypatch.setattr(enc_mod.subprocess, "run", switch_path_during_probe)
    enc_mod.detect_encoders(force=True)

    assert isolated_cache.exists()
    assert not redirected.exists()


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


def test_cache_invalidated_when_candidate_schema_changes(isolated_cache: Path) -> None:
    isolated_cache.write_text(json.dumps({
        "schema_version": 1,
        "version_key": "same-version",
        "written_at": 9_999_999_999,
        "candidates": [{
            "name": "av1_vulkan",
            "vendor": "vulkan",
            "codec": "av1",
            "works": True,
            "max_parallel": 2,
            "error": None,
        }],
    }))

    assert enc_mod._load_cache("same-version") is None


def test_failed_probe_records_error(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    monkeypatch.setattr(enc_mod.subprocess, "run", _stub_run({}))
    cands = enc_mod.detect_encoders(force=True)
    failed = [c for c in cands if not c.works]
    assert all(c.error for c in failed)


def test_libaom_discovery_uses_probe_only_speed_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_run(
        cmd: list[str], **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        captured.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(enc_mod.subprocess, "run", fake_run)
    candidate = enc_mod._probe_one("libaom-av1", "libaom", "av1")

    assert candidate.works is True
    assert captured[captured.index("-cpu-used") + 1] == "8"
    assert captured[captured.index("-row-mt") + 1] == "1"


def test_pick_encoder_prefers_explicit() -> None:
    cands = [
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True),
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]
    assert (
        enc_mod.pick_encoder(cands, prefer=["libx264"], codec="h264").name == "libx264"
    )


def test_explicit_encoder_override_ignores_auto_policy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(enc_mod.ENCODER_POLICY_ENV, "invalid-auto-policy")
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]

    selected = enc_mod.pick_encoder(
        cands,
        prefer=["libx264"],
        codec="h264",
        require_preferred=True,
    )

    assert selected.name == "libx264"


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


def test_pick_encoder_defaults_to_quality_policy(
    isolated_cache: Path,
) -> None:
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True),
    ]
    assert enc_mod.pick_encoder(cands, codec="h264").name == "libx264"


def test_pick_encoder_speed_policy_prefers_hardware() -> None:
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
        EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True),
    ]

    assert enc_mod.pick_encoder(cands, codec="h264", policy="speed").name == "h264_nvenc"


def test_pick_encoder_av1_policies_are_distinct() -> None:
    cands = [
        EncoderCandidate(name="av1_nvenc", vendor="nvenc", codec="av1", works=True),
        EncoderCandidate(name="libsvtav1", vendor="svtav1", codec="av1", works=True),
        EncoderCandidate(name="libaom-av1", vendor="libaom", codec="av1", works=True),
    ]

    assert enc_mod.pick_encoder(cands, codec="av1", policy="quality").name == "libaom-av1"
    assert enc_mod.pick_encoder(cands, codec="av1", policy="balanced").name == "libsvtav1"
    assert enc_mod.pick_encoder(cands, codec="av1", policy="speed").name == "av1_nvenc"


def test_pick_encoder_rejects_unavailable_required_override() -> None:
    cands = [
        EncoderCandidate(
            name="h264_nvenc", vendor="nvenc", codec="h264", works=False,
            error="device unavailable",
        ),
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]

    with pytest.raises(EncoderError, match="h264_nvenc.*device unavailable"):
        enc_mod.pick_encoder(
            cands,
            prefer=["h264_nvenc"],
            codec="h264",
            require_preferred=True,
        )


def test_pick_encoder_rejects_invalid_policy() -> None:
    cands = [
        EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True),
    ]

    with pytest.raises(EncoderError, match="encoder policy"):
        enc_mod.pick_encoder(cands, codec="h264", policy="fastest")


def test_cache_file_atomic_write(
    monkeypatch: pytest.MonkeyPatch,
    isolated_cache: Path,
    stub_ffmpeg_version: None,
) -> None:
    monkeypatch.setattr(enc_mod.subprocess, "run", _stub_run({"libx264": 0}))
    enc_mod.detect_encoders(force=True)
    payload = json.loads(isolated_cache.read_text())
    assert payload["schema_version"] == enc_mod.ENCODER_CACHE_SCHEMA_VERSION
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


def test_runtime_invalidation_forces_exact_capability_reprobe(
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

    assert enc_mod.probe_encoder_for_plan(plan).supported is True
    assert enc_mod.probe_encoder_for_plan(plan).supported is True
    assert run.call_count == 1

    assert enc_mod.invalidate_encoder_capability(plan) is True
    assert enc_mod.invalidate_encoder_capability(plan) is False
    assert enc_mod.probe_encoder_for_plan(plan).supported is True
    assert run.call_count == 2
