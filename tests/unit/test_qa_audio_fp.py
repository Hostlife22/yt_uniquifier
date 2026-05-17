"""Unit tests for audio fingerprint similarity via fpcalc."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core.qa import audio_fp


def _fake_fingerprint(seed: int, n: int = 50) -> str:
    header = bytes([1, 0, 0, 0])  # fake chromaprint header
    payload = b"".join(((i + seed) % (2**32)).to_bytes(4, "big") for i in range(n))
    return base64.urlsafe_b64encode(header + payload).decode().rstrip("=")


def test_fpcalc_missing_returns_unavailable(monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    monkeypatch.setattr(audio_fp.shutil, "which", lambda _name: None)
    a = tmp_path / "a.wav"

    a.touch()
    b = tmp_path / "b.wav"

    b.touch()
    res = audio_fp.compare(a, b)
    assert not res.available
    assert res.similarity is None
    assert res.note is not None and "fpcalc" in res.note


def test_identical_fingerprints_high_similarity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(audio_fp.shutil, "which", lambda _name: "/usr/bin/fpcalc")
    fp = _fake_fingerprint(seed=0)
    monkeypatch.setattr(
        audio_fp, "_run_fpcalc",
        lambda _p: {"duration": 10, "fingerprint": fp},
    )
    a = tmp_path / "a.wav"

    a.touch()
    b = tmp_path / "b.wav"

    b.touch()
    res = audio_fp.compare(a, b)
    assert res.available
    assert res.similarity is not None and res.similarity > 0.99


def test_different_fingerprints_lower_similarity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(audio_fp.shutil, "which", lambda _name: "/usr/bin/fpcalc")
    fp_a = _fake_fingerprint(seed=0)
    fp_b = _fake_fingerprint(seed=999_999)

    state = {"call": 0}

    def fake_run(_p: Path) -> dict[str, Any]:
        state["call"] += 1
        return {"duration": 10, "fingerprint": fp_a if state["call"] == 1 else fp_b}

    monkeypatch.setattr(audio_fp, "_run_fpcalc", fake_run)
    a = tmp_path / "a.wav"

    a.touch()
    b = tmp_path / "b.wav"

    b.touch()
    res = audio_fp.compare(a, b)
    assert res.available
    assert res.similarity is not None and res.similarity < 0.5


def test_malformed_fpcalc_output(monkeypatch: pytest.MonkeyPatch,
                                  tmp_path: Path) -> None:
    monkeypatch.setattr(audio_fp.shutil, "which", lambda _name: "/usr/bin/fpcalc")
    monkeypatch.setattr(audio_fp, "_run_fpcalc", lambda _p: {"duration": 10})
    a = tmp_path / "a.wav"

    a.touch()
    b = tmp_path / "b.wav"

    b.touch()
    res = audio_fp.compare(a, b)
    assert not res.available
    assert res.note is not None and "malformed" in res.note
