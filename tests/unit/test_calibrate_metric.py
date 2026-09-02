"""v0.8.0 R6 — calibration metric dispatch (chromaprint vs sscd).

Verifies that the new ``metric`` kwarg routes to the correct evaluator,
the ``evaluator=`` test seam bypasses real model loading, direct SSCD
similarity semantics are preserved, and cancel still wins
the race with mid-iteration ML work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yt_uniquifier.core.calibration import loop as loop_mod
from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget,
    _evaluate_chromaprint,
    _evaluate_sscd,
    calibrate,
)
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.runner import CancelToken

# ----- shared fixtures -----------------------------------------------------


@dataclass
class _Bookkeeping:
    """Counts evaluator invocations + records (source, candidate) pairs."""
    calls: int = 0
    sources: list[Path] | None = None
    candidates: list[Path] | None = None

    def __post_init__(self) -> None:
        self.sources = []
        self.candidates = []


def _profile() -> Profile:
    return Profile(name="t6", transforms=[
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
    ])


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the encode side so calibration is pure-Python."""
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda src, wd, sec: src)

    class _FakePlan:
        plan_hash = "fake6"

    monkeypatch.setattr(loop_mod, "build_plan", lambda *_a, **_kw: _FakePlan())
    monkeypatch.setattr(loop_mod, "run_full", lambda *_a, **_kw: None)

    class _Q:
        value = 92.0
        metric = "vmaf"
        raw = 92.0
        note = None

    monkeypatch.setattr(loop_mod, "quality_score", lambda *_a, **_kw: _Q())


# ----- chromaprint default path ------------------------------------------


def test_default_metric_is_chromaprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting metric must select the chromaprint evaluator."""
    _patch_pipeline(monkeypatch)
    book = _Bookkeeping()

    class _CID:
        match_probability_self = 0.10

    def _fake_predict(_src: Path, _out: Path):
        book.calls += 1
        return _CID()

    monkeypatch.setattr(loop_mod, "predict", _fake_predict)

    res = calibrate(
        tmp_path / "x.mp4", _profile(),
        CalibrationTarget(max_self_match=0.2, min_quality=88),
        work_dir=tmp_path / "w",
    )
    assert res.converged
    assert book.calls == 1
    assert res.final_self_match == pytest.approx(0.10)


def test_chromaprint_evaluator_unit() -> None:
    """``_evaluate_chromaprint`` returns ``predict().match_probability_self``."""
    from yt_uniquifier.core.calibration import loop as lm

    class _CID:
        match_probability_self = 0.42

    captured: list[tuple[Path, Path]] = []

    def _fake_predict(src: Path, out: Path):
        captured.append((src, out))
        return _CID()

    orig = lm.predict
    lm.predict = _fake_predict  # type: ignore[assignment]
    try:
        v = _evaluate_chromaprint(Path("a.mp4"), Path("b.mp4"), None)
    finally:
        lm.predict = orig  # type: ignore[assignment]

    assert v == pytest.approx(0.42)
    assert captured == [(Path("a.mp4"), Path("b.mp4"))]


# ----- sscd path via evaluator injection ----------------------------------


def test_metric_sscd_via_injected_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``evaluator=`` overrides metric dispatch, so torch never loads."""
    _patch_pipeline(monkeypatch)

    # Trip-wire: if anything reaches into sscd or chromaprint defaults,
    # the test fails loudly with a clear message.
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("real evaluator must not be invoked")

    monkeypatch.setattr(loop_mod, "predict", _boom)

    seen: list[tuple[Path, Path, CancelToken | None]] = []

    def _stub_eval(src: Path, cand: Path, ct: CancelToken | None) -> float:
        seen.append((src, cand, ct))
        return 0.15

    res = calibrate(
        tmp_path / "x.mp4", _profile(),
        CalibrationTarget(max_self_match=0.2, min_quality=88),
        work_dir=tmp_path / "w",
        metric="sscd",            # would route to _evaluate_sscd...
        evaluator=_stub_eval,     # ...but the seam wins.
    )
    assert res.converged
    assert res.final_self_match == pytest.approx(0.15)
    assert len(seen) == 1


def test_evaluate_sscd_returns_mean_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_evaluate_sscd`` returns direct mean similarity clamped to [0, 1]."""
    captured: list[tuple[Path, Path, CancelToken | None]] = []

    class _StubResult:
        def __init__(self, mean: float) -> None:
            self.mean_similarity = mean
            self.min_similarity = mean
            self.per_frame = (mean,)

    def _fake_compute(src: Path, out: Path, *, cancel_token=None, **_kw):
        captured.append((src, out, cancel_token))
        return _StubResult(0.92)

    # Patch the symbol *inside* yt_uniquifier.core.qa.sscd because
    # _evaluate_sscd imports it lazily; monkeypatch the module attr.
    import yt_uniquifier.core.qa.sscd as sscd_mod

    monkeypatch.setattr(sscd_mod, "compute_sscd", _fake_compute)

    ct = CancelToken()
    v = _evaluate_sscd(Path("src.mp4"), Path("out.mp4"), ct)

    assert v == pytest.approx(0.92)
    assert 0.0 <= v <= 1.0
    assert captured == [(Path("src.mp4"), Path("out.mp4"), ct)]


def test_evaluate_sscd_clamps_pathological_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathological cosine values are clamped to the public [0, 1] range."""
    import yt_uniquifier.core.qa.sscd as sscd_mod

    class _Negative:
        mean_similarity = -0.30
        min_similarity = -0.30
        per_frame = (-0.30,)

    class _OverUnity:
        mean_similarity = 1.05
        min_similarity = 1.05
        per_frame = (1.05,)

    monkeypatch.setattr(sscd_mod, "compute_sscd",
                        lambda *_a, **_kw: _Negative())
    assert _evaluate_sscd(Path("a"), Path("b"), None) == 0.0

    monkeypatch.setattr(sscd_mod, "compute_sscd",
                        lambda *_a, **_kw: _OverUnity())
    assert _evaluate_sscd(Path("a"), Path("b"), None) == 1.0


# ----- bisect convergence with sscd-style evaluator -----------------------


def test_bisect_converges_with_scripted_sscd_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSCD-style scores drive the same factor *=1.5 ramp as chromaprint."""
    _patch_pipeline(monkeypatch)

    # Three iterations: collision risk 0.55 → 0.35 → 0.18, target 0.2.
    scores = iter([0.55, 0.35, 0.18])

    def _evil(_src: Path, _cand: Path, _ct: CancelToken | None) -> float:
        return next(scores)

    res = calibrate(
        tmp_path / "x.mp4", _profile(),
        CalibrationTarget(max_self_match=0.2, min_quality=88,
                          max_iterations=5),
        work_dir=tmp_path / "w",
        metric="sscd",
        evaluator=_evil,
    )
    assert res.converged
    assert len(res.steps) == 3
    assert res.steps[0].intensity_factor == pytest.approx(1.0)
    assert res.steps[1].intensity_factor == pytest.approx(1.5)
    assert res.steps[2].intensity_factor == pytest.approx(2.25)
    assert res.final_self_match == pytest.approx(0.18)


# ----- cancel honoured mid-loop -------------------------------------------


def test_cancel_token_stops_sscd_bisection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel raised mid-iteration must propagate, not be swallowed."""
    _patch_pipeline(monkeypatch)
    ct = CancelToken()

    def _cancelling_eval(
        _src: Path, _cand: Path, _ct: CancelToken | None,
    ) -> float:
        # Simulate the cancel firing inside `compute_sscd`.
        ct.cancel()
        raise PipelineError("SSCD cancelled by user (during embed)")

    with pytest.raises(PipelineError, match="cancelled"):
        calibrate(
            tmp_path / "x.mp4", _profile(),
            CalibrationTarget(max_self_match=0.2, min_quality=88,
                              max_iterations=5),
            work_dir=tmp_path / "w",
            metric="sscd",
            evaluator=_cancelling_eval,
            cancel_token=ct,
        )


def test_pre_iteration_cancel_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token cancelled before iteration 1 must abort with no evaluator calls."""
    _patch_pipeline(monkeypatch)
    ct = CancelToken()
    ct.cancel()

    def _never(*_a: object, **_kw: object) -> float:
        raise AssertionError("evaluator must not run after cancel")

    with pytest.raises(PipelineError, match="cancelled"):
        calibrate(
            tmp_path / "x.mp4", _profile(),
            CalibrationTarget(),
            work_dir=tmp_path / "w",
            metric="sscd",
            evaluator=_never,
            cancel_token=ct,
        )


# ----- metric dispatch contract -------------------------------------------


def test_unknown_metric_raises_pipeline_error(tmp_path: Path) -> None:
    """Calibration with a bogus metric string must fail fast at dispatch."""
    with pytest.raises(PipelineError, match="unknown calibration metric"):
        calibrate(
            tmp_path / "x.mp4", _profile(),
            CalibrationTarget(),
            work_dir=tmp_path / "w",
            metric="not-a-real-metric",  # type: ignore[arg-type]
        )


def test_evaluator_kwarg_takes_priority_over_metric_for_chromaprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`evaluator=` overrides the metric even for the default value."""
    _patch_pipeline(monkeypatch)

    monkeypatch.setattr(loop_mod, "predict",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            AssertionError("default predict must be skipped")))

    res = calibrate(
        tmp_path / "x.mp4", _profile(),
        CalibrationTarget(max_self_match=0.2, min_quality=88),
        work_dir=tmp_path / "w",
        evaluator=lambda *_a, **_kw: 0.10,
    )
    assert res.converged
    assert res.final_self_match == pytest.approx(0.10)
