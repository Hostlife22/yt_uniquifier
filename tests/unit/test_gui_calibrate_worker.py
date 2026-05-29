"""CalibrateWorker — step signal + completed/finished payloads."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from yt_uniquifier.core.calibration.loop import (
    CalibratedResult,
    CalibrationStep,
    CalibrationTarget,
)
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.gui.workers.calibrate_worker import CalibrateWorker


def _profile() -> Profile:
    return Profile(name="t", transforms=[TransformConfig(id="video.crop_resize")])


def test_calibrate_worker_emits_step_per_iteration(tmp_path: Path) -> None:
    target = CalibrationTarget(max_self_match=0.2, min_quality=80.0, max_iterations=3)
    profile = _profile()

    steps_out: list[dict] = []

    def fake_calibrate(*, on_step, **_kw):  # type: ignore[no-untyped-def]
        for i in range(1, 4):
            on_step(CalibrationStep(
                iteration=i,
                intensity_factor=1.0 * i,
                profile=profile,
                self_match=0.5 / i,
                quality=90.0 - i,
                quality_metric=None,
                duration_sec=1.0,
            ))
        return CalibratedResult(
            profile=profile, factor=3.0, steps=[],
            converged=True, final_self_match=0.16, final_quality=87.0,
            final_quality_metric=None,
        )

    with patch(
        "yt_uniquifier.gui.workers.calibrate_worker._calibrate",
        side_effect=fake_calibrate,
    ):
        worker = CalibrateWorker(tmp_path / "in.mp4", profile, target)
        worker.step.connect(steps_out.append)
        worker.run()
    assert len(steps_out) == 3
    assert steps_out[0]["iteration"] == 1
    assert steps_out[2]["self_match"] < steps_out[0]["self_match"]


def test_calibrate_worker_finished_payload(tmp_path: Path) -> None:
    target = CalibrationTarget()
    profile = _profile()

    def fake_calibrate(**_kw: object) -> CalibratedResult:
        return CalibratedResult(
            profile=profile, factor=2.0, steps=[],
            converged=True, final_self_match=0.18, final_quality=88.5,
            final_quality_metric=None,
        )

    payload: list[object] = []
    completed: list[object] = []
    with patch(
        "yt_uniquifier.gui.workers.calibrate_worker._calibrate",
        side_effect=fake_calibrate,
    ):
        worker = CalibrateWorker(tmp_path / "in.mp4", profile, target)
        worker.finished_ok.connect(payload.append)
        worker.completed.connect(completed.append)
        worker.run()
    assert len(payload) == 1
    p = payload[0]
    assert isinstance(p, dict)
    assert p["factor"] == 2.0
    assert p["converged"] is True
    assert completed == [profile]


def test_calibrate_worker_failed_on_exception(tmp_path: Path) -> None:
    target = CalibrationTarget()
    with patch(
        "yt_uniquifier.gui.workers.calibrate_worker._calibrate",
        side_effect=RuntimeError("boom"),
    ):
        worker = CalibrateWorker(tmp_path / "in.mp4", _profile(), target)
        errors: list[str] = []
        worker.failed.connect(errors.append)
        worker.run()
    assert errors and "boom" in errors[0]
