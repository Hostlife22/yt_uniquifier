"""Accepted QA contract: explicit evidence, independent gates and legacy compatibility."""

from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pydantic import ValidationError

from tests.unit.test_pipeline_graph import _plan, _src
from tests.unit.test_qa_report import _FakePHash, _report
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.media_validation import (
    DecodeEvidence,
    MediaInvariantFailure,
    MediaInvariantReport,
)
from yt_uniquifier.core.models import (
    QAAudioLoudness,
    QACorrectness,
    QAQualityPolicy,
    QARegistration,
    QARegistrationDetail,
    QAReport,
    TransformConfig,
)
from yt_uniquifier.core.qa import loudness, vmaf
from yt_uniquifier.core.qa import report as report_mod
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement


def test_legacy_json_remains_readable() -> None:
    raw = _report().model_dump(exclude={"correctness", "loudness", "quality_policy"})
    parsed = QAReport.model_validate(raw)
    assert parsed.correctness is None and parsed.loudness is None
    assert report_mod.verdict(parsed).band == "green"


@pytest.mark.parametrize("value,expected", [
    ("0", 0), ("0.5", 0.5), ("100", 100), ("5e-1", 0.5),
    ("nan", None), ("inf", None), ("101", None), ("-1", None), ("0.5oops", None),
])
def test_vmaf_preserves_valid_low_scores(monkeypatch, tmp_path, value, expected) -> None:
    monkeypatch.setattr(vmaf, "vmaf_available", lambda: True)
    monkeypatch.setattr(vmaf.subprocess, "run", lambda *a, **k: CompletedProcess(
        a, 0, stdout="", stderr=f"VMAF score: {value}\n",
    ))
    result = vmaf.compute(tmp_path / "a", tmp_path / "b")
    assert result.score == expected
    assert (result.note is None) == (expected is not None)


@pytest.mark.parametrize("vmaf_value,ssim_value,expected", [
    (90, 0.99, "pass"), (0.5, 1, "fail"), (None, 1, "fail"),
    (100, None, "fail"), (100, 0.5, "fail"), (90, 0.95, "pass"),
])
def test_both_minimums_are_independent(vmaf_value, ssim_value, expected) -> None:
    report = _report(vmaf_mean=vmaf_value, ssim_mean=ssim_value,
                     quality_policy=QAQualityPolicy(min_vmaf=90, min_ssim=0.95))
    assert report_mod.verdict(report).quality == expected


@pytest.mark.parametrize("field,value", [
    ("min_vmaf", float("nan")), ("min_vmaf", float("inf")), ("min_vmaf", 101),
    ("min_ssim", -1.01), ("min_ssim", float("nan")),
])
def test_invalid_policy_rejected(field, value) -> None:
    with pytest.raises(ValidationError):
        QAQualityPolicy.model_validate({field: value})


def test_registered_gates_use_declared_domain_and_provenance() -> None:
    report = _report(
        vmaf_mean=0, ssim_mean=0, vmaf_registered_mean=95, ssim_registered_mean=0.99,
        quality_policy=QAQualityPolicy(domain="registered", min_vmaf=90, min_ssim=0.98),
    )
    assert report_mod.verdict(report).quality == "fail"
    report = report.model_copy(update={"registration": QARegistration(
        reference_mode="plan_transformed", plan_hash="abc", run_seed=1,
        video=QARegistrationDetail(compared_samples=10, coverage_ratio=1, confidence=1),
    )})
    assert report_mod.verdict(report).quality == "pass"


@pytest.mark.parametrize("status,expected", [("failed", "invalid"), ("not_verified", "yellow")])
def test_quality_cannot_hide_correctness(status, expected) -> None:
    report = _report(correctness=QACorrectness(
        status=status, scope="pair_contract", failure_codes=["output.decode"]
        if status == "failed" else [],
    ), quality_policy=QAQualityPolicy(min_vmaf=80))
    assert report_mod.verdict(report).band == expected


@pytest.mark.parametrize("decode,failure,status", [
    (False, None, "not_verified"), (True, None, "passed"),
    (True, MediaInvariantFailure("output.decode", "valid", "corrupt"), "failed"),
])
def test_build_report_emits_actual_evidence(monkeypatch, tmp_path, decode, failure, status):
    source = _src(tmp_path)
    monkeypatch.setattr(report_mod, "probe_file", lambda p: source)
    monkeypatch.setattr(report_mod, "inspect_output_decode", lambda *a, **k: failure)
    monkeypatch.setattr(report_mod.hashes, "md5_file", lambda p: "abc")
    monkeypatch.setattr(report_mod.phash, "compare", lambda *a, **k: _FakePHash())
    report = report_mod.build_report(
        source.path, source.path, verify_decode=decode, run_vmaf=False,
        run_ssim=False, run_audio_fp=False, predict_cid=False,
    )
    assert report.correctness.status == status
    assert report.loudness.status == "not_verified"
    if failure:
        assert "output.decode" in report.correctness.failure_codes


@pytest.mark.parametrize("kwargs", [
    {"run_vmaf": False, "quality_policy": QAQualityPolicy(min_vmaf=80)},
    {"run_ssim": False, "quality_policy": QAQualityPolicy(min_ssim=0.9)},
    {"quality_policy": QAQualityPolicy(domain="registered", min_ssim=0.9)},
])
def test_incompatible_policy_fails_before_probe(tmp_path, kwargs) -> None:
    with pytest.raises(PipelineError):
        report_mod.build_report(tmp_path / "missing", tmp_path / "missing", **kwargs)


@pytest.mark.parametrize("integrated,peak,status", [
    (-14.0, -1.5, "passed"), (-float("inf"), -float("inf"), "passed"),
    (float("nan"), float("inf"), "not_verified"),
])
def test_loudness_nonfinite_json_and_selection(monkeypatch, tmp_path, integrated, peak, status):
    calls = []
    def measure(*a, **kwargs):
        calls.append(kwargs)
        return LoudnormMeasurement(input_i=integrated, input_tp=peak, input_lra=0,
                                   input_thresh=-70, target_offset=0)
    monkeypatch.setattr(loudness, "measure", measure)
    result = loudness.measure_output(tmp_path / "out", _src(tmp_path))
    assert result.status == status
    assert "[0:1]" in calls[0]["pre_filter_complex"]
    assert "NaN" not in result.model_dump_json() and "Infinity" not in result.model_dump_json()
    assert result.streams[0].method == "ffmpeg_loudnorm_full_decode"


def test_loudness_cancel_is_not_swallowed(tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel()
    with pytest.raises(PipelineError, match="cancelled"):
        loudness.measure_output(tmp_path / "out", _src(tmp_path), cancel_token=token)


def test_loudness_model_rejects_nonfinite_numbers() -> None:
    with pytest.raises(ValidationError):
        QAAudioLoudness(stream_index=1, status="passed", true_peak_dbtp=float("nan"))


@pytest.fixture
def stub_report(monkeypatch, tmp_path):
    source = _src(tmp_path)
    calls = []
    monkeypatch.setattr(report_mod, "probe_file", lambda p: source)
    def decode(*a, **kwargs):
        calls.append("decode")
        return None
    monkeypatch.setattr(report_mod, "inspect_output_decode", decode)
    monkeypatch.setattr(report_mod.hashes, "md5_file", lambda p: "abc")
    monkeypatch.setattr(report_mod.phash, "compare", lambda *a, **k: _FakePHash())
    def build(output=None, **kwargs):
        return report_mod.build_report(
            source.path, output or source.path, run_vmaf=False, run_ssim=False,
            run_audio_fp=False, predict_cid=False, **kwargs,
        )
    return source, calls, build


def test_decode_evidence_survives_atomic_publication(stub_report, tmp_path):
    source, calls, build = stub_report
    evidence = DecodeEvidence.capture(source.path)
    output = source.path.rename(tmp_path / "published.mp4")
    report = build(output, verify_decode=False, decode_evidence=evidence)
    assert not calls
    assert report.correctness.full_decode_status == "passed"


def test_stale_decode_evidence_cannot_skip_validation(stub_report):
    source, calls, build = stub_report
    evidence = DecodeEvidence.capture(source.path)
    source.path.write_bytes(b"different content")
    report = build(verify_decode=False, decode_evidence=evidence)
    assert calls == ["decode"]
    assert report.correctness.full_decode_status == "passed"


def test_output_change_during_qa_invalidates_result(stub_report, monkeypatch):
    source, _, build = stub_report
    def compare(*args, **kwargs):
        source.path.write_bytes(b"changed after decode")
        return _FakePHash()
    monkeypatch.setattr(report_mod.phash, "compare", compare)
    result = build()
    assert result.correctness.status == "failed"
    assert "output.changed_during_qa" in result.correctness.failure_codes


def test_plan_speed_contract_overrides_legacy_duration_boolean(stub_report, monkeypatch):
    source, _, build = stub_report
    plan = _plan(source, [TransformConfig(id="video.speed", params={"rate": 2})])
    metas = iter([source, source.model_copy(update={"duration_sec": 2.5})])
    monkeypatch.setattr(report_mod, "probe_file", lambda p: next(metas))
    monkeypatch.setattr(report_mod, "inspect_output_contract", lambda *a, **k:
                        MediaInvariantReport(source.path, ()))
    result = build(plan=plan)
    assert not result.duration_match
    assert result.correctness.status == "passed"
    assert report_mod.verdict(result).correctness == "valid"


def test_raw_hdr_vmaf_gate_rejected_before_metric(monkeypatch, tmp_path):
    source = _src(tmp_path, hdr=True)
    monkeypatch.setattr(report_mod, "probe_file", lambda p: source)
    with pytest.raises(PipelineError, match="SDR"):
        report_mod.build_report(
            source.path, source.path, quality_policy=QAQualityPolicy(min_vmaf=80),
        )


def test_inconsistent_correctness_json_is_rejected():
    with pytest.raises(ValidationError):
        QACorrectness(status="passed", scope="pair_contract", full_decode_status="not_verified")


def test_only_explicitly_requested_metric_controls_gate():
    result = report_mod.verdict(_report(
        vmaf_mean=None, ssim_mean=-0.5, quality_policy=QAQualityPolicy(min_ssim=-0.7),
    ))
    assert result.quality == "pass"
    assert result.correctness == "not_verified"


def test_loudness_partial_backend_failure_is_visible_and_redacted(monkeypatch, tmp_path):
    calls = []
    def measure(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise PipelineError("decoder failed for /Users/alice/private/secret.mkv")
        return LoudnormMeasurement(input_i=-14, input_tp=-2, input_lra=1,
                                   input_thresh=-30, target_offset=0)
    monkeypatch.setattr(loudness, "measure", measure)
    result = loudness.measure_output(tmp_path / "out", _src(tmp_path, n_audio=2))
    assert result.status == "not_verified"
    assert [s.status for s in result.streams] == ["passed", "not_verified"]
    assert "/Users/alice" not in result.model_dump_json()


def test_loudness_cancel_during_measurement_is_propagated(monkeypatch, tmp_path):
    token = CancelToken()
    def measure(*args, **kwargs):
        token.cancel()
        raise PipelineError("stopped")
    monkeypatch.setattr(loudness, "measure", measure)
    with pytest.raises(PipelineError, match="cancelled"):
        loudness.measure_output(tmp_path / "out", _src(tmp_path), cancel_token=token)


def test_no_audio_is_not_a_failed_measurement(tmp_path):
    result = loudness.measure_output(tmp_path / "out", _src(tmp_path, n_audio=0))
    assert result.status == "passed" and result.streams == []
    assert result.note == "No output audio streams."
