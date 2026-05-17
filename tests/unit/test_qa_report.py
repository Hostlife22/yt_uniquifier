"""Tests for QA report aggregation, verdict thresholds, JSON + HTML rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yt_uniquifier.core.models import QAReport
from yt_uniquifier.core.qa import report as report_mod


def _report(**overrides: object) -> QAReport:
    base: dict[str, object] = {
        "input_md5": "a" * 32,
        "output_md5": "b" * 32,
        "input_size_bytes": 100,
        "output_size_bytes": 100,
        "input_duration_sec": 10.0,
        "output_duration_sec": 10.0,
        "phash_samples": 5,
        "phash_distance_min": 0,
        "phash_distance_mean": 0.0,
        "phash_distance_max": 0,
        "phash_similarity": 0.92,
        "audio_fp_similarity": 0.95,
        "vmaf_mean": 90.0,
        "ssim_mean": 0.99,
        "duration_match": True,
        "notes": [],
    }
    base.update(overrides)
    return QAReport.model_validate(base)


# --- verdict thresholds ------------------------------------------------------

def test_verdict_green_when_balanced() -> None:
    r = _report()
    v = report_mod.verdict(r)
    assert v.band == "yellow"  # 0.92 sits in the 0.85..0.97 band


def test_verdict_red_when_phash_too_high() -> None:
    r = _report(phash_similarity=0.99)
    assert report_mod.verdict(r).band == "red"


def test_verdict_red_when_phash_too_low() -> None:
    r = _report(phash_similarity=0.30)
    assert report_mod.verdict(r).band == "red"


def test_verdict_red_when_vmaf_low() -> None:
    r = _report(phash_similarity=0.92, vmaf_mean=70.0)
    assert report_mod.verdict(r).band == "red"


def test_verdict_yellow_when_vmaf_moderate() -> None:
    r = _report(phash_similarity=0.80, vmaf_mean=82.0)
    v = report_mod.verdict(r)
    assert v.band == "yellow"


# --- build_report aggregation ------------------------------------------------

@dataclass
class _FakePHash:
    samples: int = 5
    distance_min: int = 0
    distance_mean: float = 0.0
    distance_max: int = 0
    similarity: float = 0.92


@dataclass
class _FakeAudioFp:
    available: bool = True
    similarity: float | None = 0.95
    note: str | None = None


@dataclass
class _FakeVmaf:
    available: bool = True
    score: float | None = 90.0
    note: str | None = None


@dataclass
class _FakeSsim:
    score: float | None = 0.99
    note: str | None = None


def test_build_report_aggregates_all(monkeypatch: pytest.MonkeyPatch,
                                     tiny_clip: Path) -> None:
    monkeypatch.setattr(report_mod.hashes, "md5_file", lambda _p: "abc")
    monkeypatch.setattr(report_mod.phash, "compare", lambda *_a, **_k: _FakePHash())
    monkeypatch.setattr(report_mod.audio_fp, "compare",
                        lambda *_a, **_k: _FakeAudioFp())
    monkeypatch.setattr(report_mod.vmaf, "compute", lambda *_a, **_k: _FakeVmaf())
    monkeypatch.setattr(report_mod.ssim, "compute", lambda *_a, **_k: _FakeSsim())

    report = report_mod.build_report(tiny_clip, tiny_clip)
    assert report.input_md5 == "abc"
    assert report.phash_similarity == 0.92
    assert report.vmaf_mean == 90.0
    assert report.ssim_mean == 0.99
    assert report.audio_fp_similarity == 0.95


def test_build_report_records_notes_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, tiny_clip: Path
) -> None:
    monkeypatch.setattr(report_mod.hashes, "md5_file", lambda _p: "abc")
    monkeypatch.setattr(report_mod.phash, "compare", lambda *_a, **_k: _FakePHash())
    monkeypatch.setattr(
        report_mod.audio_fp, "compare",
        lambda *_a, **_k: _FakeAudioFp(available=False, similarity=None,
                                        note="fpcalc not in PATH"),
    )
    monkeypatch.setattr(
        report_mod.vmaf, "compute",
        lambda *_a, **_k: _FakeVmaf(score=None, note="libvmaf missing"),
    )
    monkeypatch.setattr(report_mod.ssim, "compute",
                        lambda *_a, **_k: _FakeSsim())

    report = report_mod.build_report(tiny_clip, tiny_clip)
    assert report.audio_fp_similarity is None
    assert report.vmaf_mean is None
    assert any("fpcalc" in n for n in report.notes)
    assert any("libvmaf" in n for n in report.notes)


def test_write_json_roundtrip(tmp_path: Path) -> None:
    r = _report()
    p = tmp_path / "out.qa.json"
    report_mod.write_json(r, p)
    assert p.exists()
    assert "input_md5" in p.read_text()


def test_render_html_writes_file(tmp_path: Path) -> None:
    r = _report()
    p = tmp_path / "out.qa.html"
    report_mod.render_html(r, plan=None, dest=p)
    html = p.read_text()
    assert "<!DOCTYPE html>" in html
    assert "Verdict" in html
    assert r.input_md5 in html
