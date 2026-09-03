"""Tests for QA report aggregation, verdict thresholds, JSON + HTML rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.media_validation import MediaInvariantFailure, MediaInvariantReport
from yt_uniquifier.core.models import Plan, QAReport
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
    assert v.band == "green"
    assert v.correctness == "valid"
    assert v.quality == "pass"
    assert v.visual_similarity == "moderate"


def test_high_phash_is_diagnostic_not_output_failure() -> None:
    r = _report(phash_similarity=0.99)
    result = report_mod.verdict(r)
    assert result.band == "green"
    assert result.visual_similarity == "high"


def test_low_phash_is_diagnostic_not_output_failure() -> None:
    r = _report(phash_similarity=0.30)
    result = report_mod.verdict(r)
    assert result.band == "green"
    assert result.visual_similarity == "low"


def test_verdict_red_when_vmaf_low() -> None:
    r = _report(phash_similarity=0.92, vmaf_mean=70.0)
    assert report_mod.verdict(r).band == "red"


def test_verdict_yellow_when_vmaf_moderate() -> None:
    r = _report(phash_similarity=0.80, vmaf_mean=82.0)
    v = report_mod.verdict(r)
    assert v.band == "yellow"


def test_verdict_red_when_duration_mismatch() -> None:
    # MED-1 (2026-05-30 test report): duration mismatch is a correctness failure,
    # not a metric drift — must never slip into GREEN.
    r = _report(
        phash_similarity=0.80,
        vmaf_mean=92.0,
        ssim_mean=0.99,
        duration_match=False,
        input_duration_sec=30.183,
        output_duration_sec=32.280,
    )
    v = report_mod.verdict(r)
    assert v.band == "invalid"
    assert v.correctness == "invalid"
    assert any("duration mismatch" in r for r in v.reasons)


def test_duration_mismatch_overrides_similarity_status() -> None:
    r = _report(phash_similarity=0.99, duration_match=False)
    v = report_mod.verdict(r)
    assert v.band == "invalid"


def test_verdict_invalid_on_media_correctness_note() -> None:
    result = report_mod.verdict(_report(
        phash_similarity=0.80,
        notes=["correctness: source main audio stream is missing from output"],
    ))
    assert result.band == "invalid"
    assert result.correctness == "invalid"
    assert any("main audio" in reason for reason in result.reasons)


def test_verdict_reports_unavailable_visual_similarity_without_samples() -> None:
    result = report_mod.verdict(_report(phash_samples=0, phash_similarity=0.0))
    assert result.band == "green"
    assert result.visual_similarity == "unavailable"


def test_verdict_quality_is_independent_from_similarity() -> None:
    result = report_mod.verdict(_report(phash_similarity=1.0, vmaf_mean=70.0))
    assert result.band == "red"
    assert result.quality == "fail"
    assert result.visual_similarity == "high"


def test_non_finite_quality_metric_cannot_pass() -> None:
    result = report_mod.verdict(_report(vmaf_mean=float("nan")))
    assert result.band == "red"
    assert result.quality == "fail"
    assert any("invalid VMAF" in reason for reason in result.quality_reasons)


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


def _audio_analysis(result: _FakeAudioFp | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        similarity=result or _FakeAudioFp(),
        hamming=SimpleNamespace(
            available=False,
            hamming_per_frame=None,
            match_confidence=None,
        ),
        variance=SimpleNamespace(
            available=False,
            hamming_per_window=None,
            variance_between_windows=None,
        ),
        coverage_note=None,
    )


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
    monkeypatch.setattr(report_mod.audio_fp, "analyze_pair",
                        lambda *_a, **_k: _audio_analysis())
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
        report_mod.audio_fp, "analyze_pair",
        lambda *_a, **_k: _audio_analysis(_FakeAudioFp(
            available=False, similarity=None, note="fpcalc not in PATH",
        )),
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


def test_build_report_passes_input_then_output_to_vmaf_and_ssim(
    monkeypatch: pytest.MonkeyPatch, tiny_clip: Path, tmp_path: Path
) -> None:
    """Regression: build_report must call vmaf.compute/ssim.compute with
    (input_path, output_path) — the reference first, distorted second.

    Swapping these silently inverts the perceptual metric (VMAF is not
    commutative; SSIM's scale2ref binds [1:v]=ref to the second -i input).
    """
    fake_input = tiny_clip
    fake_output = tmp_path / "out.mp4"
    fake_output.write_bytes(fake_input.read_bytes())

    vmaf_calls: list[tuple[Path, Path]] = []
    ssim_calls: list[tuple[Path, Path]] = []

    def _vmaf_spy(inp: Path, out: Path, **_k: object) -> _FakeVmaf:
        vmaf_calls.append((inp, out))
        return _FakeVmaf()

    def _ssim_spy(inp: Path, out: Path, **_k: object) -> _FakeSsim:
        ssim_calls.append((inp, out))
        return _FakeSsim()

    monkeypatch.setattr(report_mod.hashes, "md5_file", lambda _p: "abc")
    monkeypatch.setattr(report_mod.phash, "compare", lambda *_a, **_k: _FakePHash())
    monkeypatch.setattr(report_mod.audio_fp, "analyze_pair",
                        lambda *_a, **_k: _audio_analysis())
    monkeypatch.setattr(report_mod.vmaf, "compute", _vmaf_spy)
    monkeypatch.setattr(report_mod.ssim, "compute", _ssim_spy)

    report_mod.build_report(fake_input, fake_output, predict_cid=False)

    assert vmaf_calls == [(fake_input, fake_output)], (
        f"vmaf.compute called with wrong arg order: {vmaf_calls}"
    )
    assert ssim_calls == [(fake_input, fake_output)], (
        f"ssim.compute called with wrong arg order: {ssim_calls}"
    )


def test_build_report_runs_probe_before_hash_and_similarity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    events: list[str] = []
    src = tmp_path / "source.mp4"
    out = tmp_path / "output.mp4"
    src.touch()
    out.touch()
    meta = SimpleNamespace(
        size_bytes=1,
        duration_sec=1.0,
        video=[SimpleNamespace(fps=24.0)],
        audio=[],
        subtitle=[],
        chapters=[],
        _auxiliary_streams=(),
    )

    def _probe(_path: Path) -> object:
        events.append("probe")
        return meta

    def _hash(_path: Path) -> str:
        events.append("md5")
        return "abc"

    def _phash(*_args: object, **_kwargs: object) -> _FakePHash:
        events.append("phash")
        return _FakePHash()

    monkeypatch.setattr(report_mod, "probe_file", _probe)
    monkeypatch.setattr(report_mod.hashes, "md5_file", _hash)
    monkeypatch.setattr(report_mod.phash, "compare", _phash)
    report_mod.build_report(
        src,
        out,
        run_vmaf=False,
        run_ssim=False,
        run_audio_fp=False,
        predict_cid=False,
        verify_decode=False,
    )
    assert events == ["probe", "probe", "md5", "md5", "phash"]


def test_build_report_uses_strict_plan_media_contract(
    monkeypatch: pytest.MonkeyPatch, tiny_clip: Path,
) -> None:
    sentinel_plan = cast(Plan, object())
    calls: list[tuple[object, Path]] = []

    def _inspect(
        plan: object,
        output: Path,
        *,
        probed_output: object,
    ) -> MediaInvariantReport:
        assert probed_output is not None
        calls.append((plan, output))
        return MediaInvariantReport(
            output=output,
            failures=(MediaInvariantFailure("streams.audio", 1, 0),),
        )

    monkeypatch.setattr(report_mod, "inspect_output_contract", _inspect)
    report = report_mod.build_report(
        tiny_clip,
        tiny_clip,
        plan=sentinel_plan,
        samples=2,
        run_vmaf=False,
        run_ssim=False,
        run_audio_fp=False,
        predict_cid=False,
        verify_decode=False,
    )
    assert calls == [(sentinel_plan, tiny_clip)]
    assert report_mod.verdict(report).band == "invalid"
    assert any("streams.audio" in note for note in report.notes)


def test_complete_decode_failure_is_a_correctness_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from yt_uniquifier.core import media_validation

    output = tmp_path / "broken.mp4"
    output.touch()

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise PipelineError("ffmpeg exited with 1; corrupt tail")

    monkeypatch.setattr(media_validation.runner, "run", _fail)
    failure = media_validation.inspect_output_decode(output)
    assert failure is not None
    assert failure.code == "output.decode"
    assert "corrupt tail" in str(failure.actual)


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
    assert "Overall output status" in html
    assert "Correctness:" in html
    assert "Visual similarity:" in html
    assert r.input_md5 in html
