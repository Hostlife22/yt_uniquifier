"""_detect_max_parallel + _nvenc_max_parallel + plumbing into EncoderCandidate."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from yt_uniquifier.core import encoder as enc_mod


def _stub_nvidia_smi(monkeypatch: pytest.MonkeyPatch, stdout: str,
                      rc: int = 0) -> None:
    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    monkeypatch.setattr(enc_mod.subprocess, "run", fake_run)


# ---- _nvenc_max_parallel --------------------------------------------------

def test_nvenc_consumer_caps_at_three(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "12000, NVIDIA GeForce RTX 3070\n")
    # 12 GB / 500 MB = 24 sessions, but consumer cap = 3.
    assert enc_mod._nvenc_max_parallel() == 3


def test_nvenc_pro_quadro_caps_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "48000, NVIDIA Quadro RTX 8000\n")
    # 48 GB / 500 MB = 96, pro cap = 8.
    assert enc_mod._nvenc_max_parallel() == 8


def test_nvenc_pro_rtx_a_caps_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "16000, NVIDIA RTX A4000\n")
    assert enc_mod._nvenc_max_parallel() == 8


def test_nvenc_pro_a100_caps_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "40000, NVIDIA A100-PCIE-40GB\n")
    assert enc_mod._nvenc_max_parallel() == 8


def test_nvenc_low_vram_uses_vram_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # 800 MB free, consumer card → max sessions by VRAM = 1, well below cap=3.
    _stub_nvidia_smi(monkeypatch, "800, NVIDIA GeForce GTX 1660\n")
    assert enc_mod._nvenc_max_parallel() == 1


def test_nvenc_no_nvidia_smi_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("nvidia-smi: not found")

    monkeypatch.setattr(enc_mod.subprocess, "run", fake_run)
    assert enc_mod._nvenc_max_parallel() == 3  # _VENDOR_DEFAULT_PARALLEL


def test_nvenc_garbage_output_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "not, parseable\n")
    assert enc_mod._nvenc_max_parallel() == 3


def test_nvenc_nonzero_rc_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_nvidia_smi(monkeypatch, "", rc=1)
    assert enc_mod._nvenc_max_parallel() == 3


# ---- _detect_max_parallel per vendor --------------------------------------

def test_detect_x264_uses_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enc_mod, "_VENDOR_DEFAULT_PARALLEL",
                         {**enc_mod._VENDOR_DEFAULT_PARALLEL})
    # Mock cpu_count to 12 → cap = 6.
    import os
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    assert enc_mod._detect_max_parallel("x264") == 6
    assert enc_mod._detect_max_parallel("x265") == 6


def test_detect_x264_floor_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert enc_mod._detect_max_parallel("x264") == 1


def test_detect_videotoolbox_default_two() -> None:
    assert enc_mod._detect_max_parallel("videotoolbox") == 2


def test_detect_qsv_default_two() -> None:
    assert enc_mod._detect_max_parallel("qsv") == 2


def test_detect_amf_default_two() -> None:
    assert enc_mod._detect_max_parallel("amf") == 2


# ---- plumbing into _probe_one --------------------------------------------

def test_working_encoder_carries_max_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enc_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        enc_mod.subprocess, "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    # Patch detection so we don't depend on host nvidia-smi behaviour.
    monkeypatch.setattr(enc_mod, "_detect_max_parallel", lambda v: 7)
    cand = enc_mod._probe_one("libx264", "x264", "h264")
    assert cand.works is True
    assert cand.max_parallel == 7


def test_failing_encoder_max_parallel_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enc_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        enc_mod.subprocess, "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 1, stdout="",
                                                        stderr="unknown encoder"),
    )
    cand = enc_mod._probe_one("h264_nvenc", "nvenc", "h264")
    assert cand.works is False
    assert cand.max_parallel == 1
