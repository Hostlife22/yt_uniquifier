"""quality_score: VMAF with SSIM fallback only when VMAF is unavailable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yt_uniquifier.core.qa import quality as quality_mod
from yt_uniquifier.core.qa.quality import quality_score


@dataclass
class _Vmaf:
    available: bool = True
    score: float | None = None
    note: str | None = None


@dataclass
class _Ssim:
    score: float | None = None
    note: str | None = None


def _patch(monkeypatch: pytest.MonkeyPatch, *,
            vmaf_score: float | None = None,
            vmaf_note: str | None = None,
            ssim_score: float | None = None,
            ssim_note: str | None = None) -> None:
    monkeypatch.setattr(quality_mod.vmaf, "compute",
                        lambda *_a, **_kw: _Vmaf(score=vmaf_score, note=vmaf_note))
    monkeypatch.setattr(quality_mod.ssim, "compute",
                        lambda *_a, **_kw: _Ssim(score=ssim_score, note=ssim_note))


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a.mp4"
    a.touch()
    b = tmp_path / "b.mp4"
    b.touch()
    return a, b


def test_vmaf_used_when_reliable(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, vmaf_score=92.0)
    a, b = _paths(tmp_path)
    res = quality_score(a, b)
    assert res.metric == "vmaf"
    assert res.value == 92.0
    assert res.note is None


def test_low_vmaf_is_not_hidden_by_ssim_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, vmaf_score=0.1, ssim_score=0.95)
    a, b = _paths(tmp_path)
    res = quality_score(a, b)
    assert res.metric == "vmaf"
    assert res.value == pytest.approx(0.1)
    assert res.note is None


def test_no_vmaf_falls_back_to_ssim(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, vmaf_score=None, vmaf_note="libvmaf missing",
           ssim_score=0.88)
    a, b = _paths(tmp_path)
    res = quality_score(a, b)
    assert res.metric == "ssim"
    assert res.value == pytest.approx(88.0)


def test_no_vmaf_no_ssim_is_not_faked_with_phash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, vmaf_score=None, vmaf_note="vmaf missing",
           ssim_score=None, ssim_note="ssim failed")
    a, b = _paths(tmp_path)
    with pytest.raises(quality_mod.PipelineError, match="no comparable quality metric"):
        quality_score(a, b)


def test_identity_pair_high_score(tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """When every metric reports max similarity, we get ~100."""
    _patch(monkeypatch, vmaf_score=100.0)
    a, b = _paths(tmp_path)
    assert quality_score(a, b).value == 100.0


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1.0, 101.0])
def test_invalid_vmaf_is_an_error_not_a_fallback_score(
    score: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, vmaf_score=score, ssim_score=0.99)
    a, b = _paths(tmp_path)
    with pytest.raises(quality_mod.PipelineError, match="invalid VMAF"):
        quality_score(a, b)
