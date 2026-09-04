"""Unit tests for VMAF / SSIM wrappers with mocked subprocess + ffmpeg lookup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.qa import ssim, vmaf
from yt_uniquifier.core.runner import CancelToken


@pytest.fixture(autouse=True)
def _ffmpeg_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vmaf, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ssim, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    vmaf.vmaf_available.cache_clear()


def _fake_run(stdout: str = "", stderr: str = "", rc: int = 0) -> Any:
    def runner(_cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(_cmd, rc, stdout=stdout, stderr=stderr)
    return runner


def test_vmaf_unavailable_when_filter_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        vmaf.subprocess, "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout="...no vmaf here...", stderr=""
        ),
    )
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = vmaf.compute(a, b)
    assert not res.available
    assert res.score is None


def test_vmaf_parses_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if "-filters" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=" libvmaf\n", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="",
            stderr="frame ... \n[Parsed_libvmaf_0 @ 0x] VMAF score: 92.31\n",
        )

    monkeypatch.setattr(vmaf.subprocess, "run", fake_run)
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = vmaf.compute(a, b)
    assert res.available
    assert res.score == pytest.approx(92.31)


def test_vmaf_nonzero_exit_yields_note(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if "-filters" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=" libvmaf\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom: bad\n")

    monkeypatch.setattr(vmaf.subprocess, "run", fake_run)
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = vmaf.compute(a, b)
    assert res.available
    assert res.score is None
    assert res.note is not None and "boom" in res.note


def test_ssim_parses_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ssim.subprocess, "run",
        _fake_run(stderr="[Parsed_ssim_0 @ 0x] SSIM Y:0.99 U:0.99 V:0.99 All:0.9923 (21dB)\n"),
    )
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = ssim.compute(a, b)
    assert res.score == pytest.approx(0.9923)


def test_ssim_missing_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ssim.subprocess, "run", _fake_run(stderr="frame=10\n"))
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = ssim.compute(a, b)
    assert res.score is None
    assert res.note is not None and "not found" in res.note


@pytest.mark.parametrize("metric", [vmaf, ssim])
def test_registered_metric_resets_both_input_timelines(
    metric: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}
    if metric is vmaf:
        monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)

    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        stderr = "VMAF score: 99.0" if metric is vmaf else "SSIM All:0.999"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr)

    monkeypatch.setattr(metric.subprocess, "run", fake_run)
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.touch()
    output.touch()

    metric.compute(source, output, reset_pts=True)

    graph = captured["cmd"][captured["cmd"].index("-lavfi") + 1]
    assert graph.count("setpts=PTS-STARTPTS") == 2


@pytest.mark.parametrize("metric", [vmaf, ssim])
def test_registered_metric_propagates_cancellation(
    metric: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from yt_uniquifier.core import runner as runner_mod

    if metric is vmaf:
        monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)
    token = CancelToken()

    def cancel_run(*_args: Any, **_kwargs: Any) -> None:
        token.cancel()
        raise PipelineError("cancelled by user")

    monkeypatch.setattr(runner_mod, "run", cancel_run)
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.touch()
    output.touch()

    with pytest.raises(PipelineError, match="cancelled"):
        metric.compute(source, output, reset_pts=True, cancel_token=token)
