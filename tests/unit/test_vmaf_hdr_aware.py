"""vmaf.compute(hdr_aware=True) plumbs phone_model=0 into libvmaf."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core.qa import vmaf


def _stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(vmaf, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)

    def fake_run(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="VMAF score: 90.0")

    monkeypatch.setattr(vmaf.subprocess, "run", fake_run)
    return captured


def test_hdr_aware_default_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub(monkeypatch)
    a = tmp_path / "a.mp4"
    a.touch()
    b = tmp_path / "b.mp4"
    b.touch()
    vmaf.compute(a, b)
    cmd = " ".join(captured["cmd"])
    assert "phone_model" not in cmd


def test_hdr_aware_true_adds_phone_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub(monkeypatch)
    a = tmp_path / "a.mp4"
    a.touch()
    b = tmp_path / "b.mp4"
    b.touch()
    vmaf.compute(a, b, hdr_aware=True)
    cmd = " ".join(captured["cmd"])
    assert ":phone_model=0" in cmd


def test_hdr_aware_combines_with_subsample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub(monkeypatch)
    a = tmp_path / "a.mp4"
    a.touch()
    b = tmp_path / "b.mp4"
    b.touch()
    vmaf.compute(a, b, subsample=5, hdr_aware=True)
    cmd = " ".join(captured["cmd"])
    assert ":n_subsample=5" in cmd
    assert ":phone_model=0" in cmd
