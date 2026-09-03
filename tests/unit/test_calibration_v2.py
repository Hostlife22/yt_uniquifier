"""Calibration v2 bounded search, representative probe, and durable cache."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yt_uniquifier.core.calibration import loop as loop_mod
from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget,
    _CachedTrial,
    _load_trial,
    _save_trial,
    _stratified_windows,
    calibrate,
)
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile, TransformConfig


def _profile() -> Profile:
    return Profile(name="v2", transforms=[
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
    ])


@dataclass
class _FakePlan:
    plan_hash: str


class _Quality:
    def __init__(self, value: float, metric: str = "vmaf") -> None:
        self.value = value
        self.metric = metric
        self.raw = value
        self.note = None


def _factor_from_profile(profile: Profile) -> float:
    strength = float(profile.transforms[0].params["max_strength"])
    return strength / 0.04


def _patch_factor_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    similarity: object,
    quality: object,
) -> tuple[dict[str, float], dict[str, int]]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"calibration-v2-source")
    state = {"factor": 1.0}
    calls = {"run": 0, "similarity": 0, "quality": 0}
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda *_a, **_kw: source)

    def _build(_clip: Path, profile: Profile, _encoder: str | None) -> _FakePlan:
        factor = _factor_from_profile(profile)
        state["factor"] = factor
        return _FakePlan(f"factor-{factor:.8f}")

    def _run(*_a: object, **_kw: object) -> None:
        calls["run"] += 1

    def _evaluate(*_a: object, **_kw: object) -> float:
        calls["similarity"] += 1
        assert callable(similarity)
        return float(similarity(state["factor"]))

    def _quality(*_a: object, **_kw: object) -> _Quality:
        calls["quality"] += 1
        assert callable(quality)
        value, metric = quality(state["factor"])
        return _Quality(float(value), str(metric))

    monkeypatch.setattr(loop_mod, "build_plan", _build)
    monkeypatch.setattr(loop_mod, "run_full", _run)
    monkeypatch.setattr(loop_mod, "_build_evaluator", lambda _metric: _evaluate)
    monkeypatch.setattr(loop_mod, "quality_score", _quality)
    return state, calls


def test_stratified_windows_keep_total_budget() -> None:
    windows = _stratified_windows(duration=120.0, budget=30.0)
    assert windows == [(0.0, 10.0), (55.0, 65.0), (110.0, 120.0)]
    assert sum(end - start for start, end in windows) == pytest.approx(30.0)


def test_short_source_uses_one_complete_window() -> None:
    assert _stratified_windows(duration=12.5, budget=60.0) == [(0.0, 12.5)]


def test_non_monotone_search_finds_feasible_interior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only a narrow island around factor=2 is feasible. The three anchors all
    # fail, so a monotone stop/ramp cannot find it; bounded interval exploration can.
    _patch_factor_pipeline(
        monkeypatch,
        tmp_path,
        similarity=lambda factor: 0.15 if 1.8 <= factor <= 2.2 else 0.55,
        quality=lambda _factor: (92.0, "vmaf"),
    )
    result = calibrate(
        tmp_path / "ignored.mp4",
        _profile(),
        CalibrationTarget(max_iterations=5),
        work_dir=tmp_path / "work",
    )
    assert result.converged
    assert result.factor == pytest.approx(2.0)
    assert [step.intensity_factor for step in result.steps] == pytest.approx(
        [1.0, 0.25, 4.0, 0.5, 2.0]
    )


def test_feasible_result_prefers_quality_over_excessive_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_factor_pipeline(
        monkeypatch,
        tmp_path,
        similarity=lambda factor: {0.25: 0.18, 1.0: 0.15, 4.0: 0.01}.get(
            round(factor, 2), 0.10
        ),
        quality=lambda factor: ({0.25: 96.0, 1.0: 94.0, 4.0: 89.0}.get(
            round(factor, 2), 90.0
        ), "vmaf"),
    )
    result = calibrate(
        tmp_path / "ignored.mp4",
        _profile(),
        CalibrationTarget(max_iterations=3),
        work_dir=tmp_path / "work",
    )
    assert result.converged
    assert result.factor == pytest.approx(0.25)
    assert result.final_quality == pytest.approx(96.0)


def test_second_run_reuses_durable_scored_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, calls = _patch_factor_pipeline(
        monkeypatch,
        tmp_path,
        similarity=lambda factor: max(0.0, 0.6 - factor * 0.12),
        quality=lambda factor: (96.0 - factor, "vmaf"),
    )
    target = CalibrationTarget(max_iterations=3)
    first = calibrate(
        tmp_path / "ignored.mp4", _profile(), target, work_dir=tmp_path / "work"
    )
    assert calls == {"run": 3, "similarity": 3, "quality": 3}

    second = calibrate(
        tmp_path / "ignored.mp4", _profile(), target, work_dir=tmp_path / "work"
    )
    assert calls == {"run": 3, "similarity": 3, "quality": 3}
    assert [step.intensity_factor for step in second.steps] == [
        step.intensity_factor for step in first.steps
    ]
    assert all(step.note and "cache hit" in step.note for step in second.steps)


def test_concurrent_sessions_do_not_share_incomplete_artifact_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    outputs: list[Path] = []
    work_dirs: list[Path] = []
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda *_a, **_kw: source)
    monkeypatch.setattr(
        loop_mod, "build_plan", lambda *_a, **_kw: _FakePlan("same-plan")
    )

    def _run(_plan: object, options: object, **_kwargs: object) -> None:
        outputs.append(options.output)  # type: ignore[attr-defined]
        work_dirs.append(options.work_dir)  # type: ignore[attr-defined]

    monkeypatch.setattr(loop_mod, "run_full", _run)
    monkeypatch.setattr(
        loop_mod, "quality_score", lambda *_a, **_kw: _Quality(92.0)
    )
    for _ in range(2):
        calibrate(
            source,
            _profile(),
            CalibrationTarget(max_iterations=1),
            work_dir=tmp_path / "shared",
            evaluator=lambda *_a, **_kw: 0.1,
        )

    assert outputs[0] != outputs[1]
    assert work_dirs[0] != work_dirs[1]


def test_corrupt_or_wrong_schema_trial_cache_is_ignored(tmp_path: Path) -> None:
    cache_dir = tmp_path / "trial_cache"
    cache_dir.mkdir()
    path = cache_dir / "abc_chromaprint.json"
    path.write_text("not-json", encoding="utf-8")
    assert _load_trial(tmp_path, "abc", "chromaprint") is None

    path.write_text('{"schema_version": 1}', encoding="utf-8")
    assert _load_trial(tmp_path, "abc", "chromaprint") is None


def test_trial_cache_round_trip(tmp_path: Path) -> None:
    trial = _CachedTrial(0.17, 91.5, "ssim", "VMAF unavailable")
    _save_trial(tmp_path, "deadbeef", "sscd", trial)
    assert _load_trial(tmp_path, "deadbeef", "sscd") == trial


def test_quality_backend_change_aborts_incomparable_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_factor_pipeline(
        monkeypatch,
        tmp_path,
        similarity=lambda _factor: 0.1,
        quality=lambda factor: (92.0, "vmaf" if factor == 1.0 else "ssim"),
    )
    with pytest.raises(PipelineError, match="quality metric changed"):
        calibrate(
            tmp_path / "ignored.mp4",
            _profile(),
            CalibrationTarget(max_iterations=2),
            work_dir=tmp_path / "work",
        )


@pytest.mark.parametrize(
    "target, message",
    [
        (CalibrationTarget(max_self_match=1.1), "max_self_match"),
        (CalibrationTarget(min_quality=-1.0), "min_quality"),
        (CalibrationTarget(max_iterations=0), "max_iterations"),
        (CalibrationTarget(test_clip_sec=0.0), "test_clip_sec"),
        (CalibrationTarget(min_factor=0.0), "factor bounds"),
        (CalibrationTarget(min_factor=2.0, max_factor=1.0), "factor bounds"),
    ],
)
def test_invalid_targets_fail_before_media_work(
    tmp_path: Path, target: CalibrationTarget, message: str,
) -> None:
    with pytest.raises(PipelineError, match=message):
        calibrate(tmp_path / "missing.mp4", _profile(), target, work_dir=tmp_path)
