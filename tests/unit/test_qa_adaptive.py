"""adaptive_n + vmaf.subsample plumbing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core.qa import phash, vmaf
from yt_uniquifier.core.qa.phash import adaptive_n

# ---- phash.adaptive_n ------------------------------------------------------

def test_adaptive_n_floor() -> None:
    assert adaptive_n(0) == 60
    assert adaptive_n(10) == 60         # 10/60 * 30 = 5 → floor 60


def test_adaptive_n_proportional() -> None:
    # 10 min → 300; capped between 60 and 600.
    assert adaptive_n(10 * 60) == 300


def test_adaptive_n_ceiling() -> None:
    # 4 h → 7200; capped at 600.
    assert adaptive_n(4 * 3600) == 600


def test_compare_uses_adaptive_n_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    def fake_sample(path: Path, n: int = 0) -> list:
        requested.append(n)
        return []

    monkeypatch.setattr(phash, "_probe_duration", lambda _p: 600.0)  # 10 min
    monkeypatch.setattr(phash, "sample_frames", fake_sample)

    src = tmp_path / "a.mp4"
    src.touch()
    out = tmp_path / "b.mp4"
    out.touch()
    phash.compare(src, out, n=None)
    assert requested[0] == 300


# ---- vmaf.subsample plumbing -----------------------------------------------

def test_vmaf_subsample_default_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="VMAF score: 92.0")

    monkeypatch.setattr(vmaf, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)
    monkeypatch.setattr(vmaf.subprocess, "run", fake_run)

    src = tmp_path / "a.mp4"
    src.touch()
    out = tmp_path / "b.mp4"
    out.touch()
    vmaf.compute(src, out)
    cmd = " ".join(captured["cmd"])
    assert "n_subsample" not in cmd


def test_vmaf_subsample_propagated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="VMAF score: 85.0")

    monkeypatch.setattr(vmaf, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)
    monkeypatch.setattr(vmaf.subprocess, "run", fake_run)

    src = tmp_path / "a.mp4"
    src.touch()
    out = tmp_path / "b.mp4"
    out.touch()
    vmaf.compute(src, out, subsample=5)
    cmd = " ".join(captured["cmd"])
    assert ":n_subsample=5" in cmd
