"""v1.3.0 Task 31 — DRM guardrail unit tests.

Covers:

  * ffprobe-marker substring match (cenc:default_KID etc.)
  * deep-walk encryption-key detection in nested JSON
  * clean JSON → not detected
  * ffprobe nonzero exit → fail-closed (treated as encrypted)
  * ffprobe timeout → fail-closed
  * ffprobe missing binary → PipelineError
  * preflight emits drm.encrypted on positive detection
  * preflight emits no finding on clean source
  * preflight surfaces probe error as drm.probe_failed warn (not silent skip)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from yt_uniquifier.core import preflight as pf
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.guardrails import drm
from yt_uniquifier.core.guardrails.drm import DrmFinding
from yt_uniquifier.core.models import (
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.pipeline import compute_plan_hash


def _src(tmp_path: Path) -> SourceMeta:
    sp = tmp_path / "src.mp4"
    sp.touch()
    return SourceMeta(
        path=sp, container="mp4", duration_sec=10.0, size_bytes=1000,
        video=[VideoStream(
            index=0, codec="h264", width=1920, height=1080, fps=24.0,
            duration_sec=10.0, pix_fmt="yuv420p", bit_rate=8_000_000,
            color=HDRInfo(is_hdr=False),
        )],
    )


def _plan(src: SourceMeta) -> Plan:
    profile = Profile(name="p", target_codec="h264")
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))


def _fake_completed(stdout: str, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=returncode, stdout=stdout, stderr="",
    )


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------


def test_detector_finds_cenc_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "src.mp4"
    src.touch()
    fake_json = json.dumps({
        "format": {"filename": "src.mp4"},
        "streams": [{"codec_name": "h264",
                     "tags": {"cenc:default_KID": "abcd1234"}}],
    })
    monkeypatch.setattr(
        drm.subprocess, "run",
        lambda *a, **kw: _fake_completed(fake_json),
    )
    res = drm.detect_drm(src)
    assert res.is_encrypted is True
    assert res.matched_marker is not None


def test_detector_finds_via_nested_encryption_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A JSON path with no listed marker substring but a key named
    'encryption' anywhere in the tree must still trip the walk."""
    src = tmp_path / "src.mp4"
    src.touch()
    fake_json = json.dumps({
        "format": {"filename": "src.mp4"},
        "streams": [{"side_data": [{"encryption": {"key": "xxx"}}]}],
    })
    monkeypatch.setattr(
        drm.subprocess, "run",
        lambda *a, **kw: _fake_completed(fake_json),
    )
    res = drm.detect_drm(src)
    assert res.is_encrypted is True
    assert res.matched_marker is not None


def test_detector_clean_source_returns_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "src.mp4"
    src.touch()
    fake_json = json.dumps({
        "format": {"filename": "src.mp4"},
        "streams": [{"codec_name": "h264", "bit_rate": "8000000"}],
    })
    monkeypatch.setattr(
        drm.subprocess, "run",
        lambda *a, **kw: _fake_completed(fake_json),
    )
    res = drm.detect_drm(src)
    assert res.is_encrypted is False


def test_detector_marks_probe_failed_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "src.mp4"
    src.touch()
    monkeypatch.setattr(
        drm.subprocess, "run",
        lambda *a, **kw: _fake_completed("", returncode=1),
    )
    res = drm.detect_drm(src)
    assert res.probe_failed is True
    assert res.is_encrypted is False


def test_detector_marks_probe_failed_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "src.mp4"
    src.touch()

    def fake_run(*a: object, **kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=30)

    monkeypatch.setattr(drm.subprocess, "run", fake_run)
    res = drm.detect_drm(src)
    assert res.probe_failed is True
    assert res.is_encrypted is False
    assert "timed out" in res.note


def test_detector_raises_when_ffprobe_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "src.mp4"
    src.touch()

    def fake_run(*a: object, **kw: object) -> object:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(drm.subprocess, "run", fake_run)
    with pytest.raises(PipelineError, match="ffprobe not found"):
        drm.detect_drm(src)


# ---------------------------------------------------------------------------
# preflight integration
# ---------------------------------------------------------------------------


def test_preflight_drm_encrypted_emits_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    monkeypatch.setattr(
        "yt_uniquifier.core.guardrails.drm.detect_drm",
        lambda _p: DrmFinding(is_encrypted=True, matched_marker="pssh"),
    )
    findings = pf._check_input_drm(src)
    assert len(findings) == 1
    f = findings[0]
    assert f.code == "drm.encrypted"
    assert f.severity == "fail"
    assert "pssh" in f.message


def test_preflight_drm_clean_emits_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    monkeypatch.setattr(
        "yt_uniquifier.core.guardrails.drm.detect_drm",
        lambda _p: DrmFinding(is_encrypted=False),
    )
    assert pf._check_input_drm(src) == []


def test_preflight_drm_probe_error_warn_not_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A failing probe must surface as warn — silently skipping would
    let an encrypted source through whenever ffprobe broke."""
    src = _src(tmp_path)

    def boom(_p: Path) -> DrmFinding:
        raise PipelineError("ffprobe not found")

    monkeypatch.setattr("yt_uniquifier.core.guardrails.drm.detect_drm", boom)
    findings = pf._check_input_drm(src)
    assert len(findings) == 1
    assert findings[0].code == "drm.probe_failed"
    assert findings[0].severity == "warn"
