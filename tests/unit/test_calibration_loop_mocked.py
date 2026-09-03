"""Calibration v2 search against scripted similarity and quality scores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yt_uniquifier.core.calibration import loop as loop_mod
from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget,
    calibrate,
)
from yt_uniquifier.core.models import Profile, TransformConfig


@dataclass
class _ScriptedState:
    """Sequenced (self_match, quality) pairs delivered to predict/quality_score."""
    pairs: list[tuple[float, float | None]]
    calls: int = 0


def _patch(monkeypatch: pytest.MonkeyPatch,
            pairs: list[tuple[float, float | None]]) -> None:
    state = _ScriptedState(pairs=pairs)

    # Skip the expensive prep steps: cut_test_clip, build_plan, run_full.
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda src, wd, sec: src)

    class _FakePlan:
        plan_hash = "fake"
    monkeypatch.setattr(loop_mod, "build_plan", lambda *_a, **_kw: _FakePlan())
    monkeypatch.setattr(loop_mod, "run_full", lambda *_a, **_kw: None)

    class _CIDResult:
        def __init__(self, m: float) -> None:
            self.match_probability_self = m

    def _predict(_in: Path, _out: Path):
        idx = state.calls
        state.calls += 1
        match, _ = state.pairs[idx % len(state.pairs)]
        return _CIDResult(match)

    monkeypatch.setattr(loop_mod, "predict", _predict)

    class _Q:
        def __init__(self, v: float | None) -> None:
            self.value = v if v is not None else 0.0
            self.metric = "vmaf"
            self.raw = v if v is not None else 0.0
            self.note = None

    def _quality_score(_a: Path, _b: Path, **_kw: object) -> _Q:
        idx = state.calls - 1
        _, q = state.pairs[idx % len(state.pairs)]
        return _Q(q)

    monkeypatch.setattr(loop_mod, "quality_score", _quality_score)


def _profile() -> Profile:
    return Profile(name="t", transforms=[
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.012}),
    ])


def test_converges_when_first_step_passes(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [(0.15, 92.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_quality=88,
                                      max_iterations=3),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert res.final_self_match == 0.15
    # Equal-quality feasible candidates prefer the gentlest bounded factor.
    assert res.factor == 0.25
    assert len(res.steps) == 3


def test_samples_baseline_and_bounds_before_refinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [(0.6, 92.0), (0.4, 91.0), (0.15, 89.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_quality=88,
                                      max_iterations=3),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert len(res.steps) == 3
    assert [step.intensity_factor for step in res.steps] == [1.0, 0.25, 4.0]
    assert res.final_self_match == 0.15


def test_returns_best_so_far_on_non_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [(0.9, 95.0), (0.6, 94.0), (0.4, 93.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.1, min_quality=88,
                                       max_iterations=3),
                    work_dir=tmp_path / "w")
    assert not res.converged
    assert res.final_self_match == 0.4
    assert res.note is not None


def test_quality_floor_backs_off(tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # iter1 self_match 0.05 OK but quality 70 < 88 → back off.
    # iter2 self_match 0.18 quality 90 → converged.
    _patch(monkeypatch, [(0.05, 70.0), (0.18, 90.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_quality=88,
                                      max_iterations=2),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert res.steps[1].intensity_factor < res.steps[0].intensity_factor


def test_iteration_exception_retries_same_factor(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    boom = iter([True, False])

    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda src, wd, sec: src)

    class _FakePlan:
        plan_hash = "fake"
    monkeypatch.setattr(loop_mod, "build_plan", lambda *a, **kw: _FakePlan())

    def _maybe_raise(*_a: object, **_kw: object) -> None:
        if next(boom):
            raise RuntimeError("nope")

    monkeypatch.setattr(loop_mod, "run_full", _maybe_raise)

    class _CIDResult:
        match_probability_self = 0.10
    monkeypatch.setattr(loop_mod, "predict", lambda *_a, **_kw: _CIDResult())

    class _Q:
        value = 92.0
        metric = "vmaf"
        raw = 92.0
        note = None
    monkeypatch.setattr(loop_mod, "quality_score",
                        lambda *_a, **_kw: _Q())

    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_iterations=1),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert len(res.steps) == 1
    assert res.factor == pytest.approx(1.0)


def test_repeated_iteration_failure_aborts_instead_of_increasing_intensity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda src, wd, sec: src)
    monkeypatch.setattr(loop_mod, "build_plan", lambda *_a, **_kw: type(
        "FakePlan", (), {"plan_hash": "fake"},
    )())
    monkeypatch.setattr(
        loop_mod, "run_full",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("encoder offline")),
    )

    with pytest.raises(Exception, match="failed twice"):
        calibrate(
            tmp_path / "x.mp4", _profile(), CalibrationTarget(max_iterations=3),
            work_dir=tmp_path / "w",
        )
