"""calibrate() bisect logic against scripted predict + vmaf."""

from __future__ import annotations

from collections.abc import Iterable
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
class _ScriptedPredict:
    """Yields successive (self_match, vmaf) pairs."""
    pairs: list[tuple[float, float | None]]
    calls: int = 0


def _patch(monkeypatch: pytest.MonkeyPatch, pairs: list[tuple[float, float | None]]) -> None:
    state = _ScriptedPredict(pairs=pairs)

    # Skip the expensive prep steps: cut_test_clip, build_plan, run_full.
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda src, wd, sec: src)

    class _FakePlan:
        plan_hash = "fake"
    monkeypatch.setattr(loop_mod, "build_plan",
                        lambda *_a, **_kw: _FakePlan())
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

    class _VMAFResult:
        def __init__(self, s: float | None) -> None:
            self.score = s

    def _vmaf_compute(_a, _b):
        idx = state.calls - 1
        _, v = state.pairs[idx % len(state.pairs)]
        return _VMAFResult(v)

    monkeypatch.setattr(loop_mod.vmaf_mod, "compute", _vmaf_compute)


def _profile() -> Profile:
    return Profile(name="t", transforms=[
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.012}),
    ])


def test_converges_when_first_step_passes(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [(0.15, 92.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_vmaf=88),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert res.final_self_match == 0.15
    assert res.factor == 1.0
    assert len(res.steps) == 1


def test_scales_up_until_target_reached(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    # iter1 0.6 vmaf 92 → scale up
    # iter2 0.4 vmaf 91 → scale up
    # iter3 0.15 vmaf 89 → converged
    _patch(monkeypatch, [(0.6, 92.0), (0.4, 91.0), (0.15, 89.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_vmaf=88,
                                       max_iterations=5),
                    work_dir=tmp_path / "w")
    assert res.converged
    assert len(res.steps) == 3
    assert res.steps[1].intensity_factor == pytest.approx(1.5)
    assert res.steps[2].intensity_factor == pytest.approx(2.25)
    assert res.final_self_match == 0.15


def test_returns_best_so_far_on_non_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Never meets target. Best is the lowest self_match step.
    _patch(monkeypatch, [(0.9, 95.0), (0.6, 94.0), (0.4, 93.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.1, min_vmaf=88,
                                       max_iterations=3),
                    work_dir=tmp_path / "w")
    assert not res.converged
    assert res.final_self_match == 0.4
    assert res.note is not None


def test_quality_floor_backs_off(tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # iter1 self_match 0.05 (under target) but vmaf 70 (below min) → back off.
    # iter2 self_match 0.18 vmaf 90 → converged.
    _patch(monkeypatch, [(0.05, 70.0), (0.18, 90.0)])
    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_self_match=0.2, min_vmaf=88,
                                       max_iterations=4),
                    work_dir=tmp_path / "w")
    assert res.converged
    # Factor should have scaled DOWN after iter1.
    assert res.steps[1].intensity_factor < res.steps[0].intensity_factor


def test_iteration_exception_continues(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    # First iter blows up in run_full; second succeeds.
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

    class _VMAFResult:
        score = 92.0
    monkeypatch.setattr(loop_mod.vmaf_mod, "compute",
                        lambda *_a, **_kw: _VMAFResult())

    res = calibrate(tmp_path / "x.mp4", _profile(),
                    CalibrationTarget(max_iterations=3),
                    work_dir=tmp_path / "w")
    # Two recorded steps (one failed, one success).
    assert len(res.steps) >= 2
    assert any("failed" in (s.note or "") for s in res.steps)


# Reference exposed primarily so the test file's imports are non-trivial.
_ = (Iterable,)
