"""Wrap core.calibration.loop.calibrate for the GUI."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.calibration.loop import (
    CalibrationStep,
    CalibrationTarget,
)
from yt_uniquifier.core.calibration.loop import (
    calibrate as _calibrate,
)
from yt_uniquifier.core.models import Profile
from yt_uniquifier.gui.workers.base import WorkerBase


def _step_as_dict(s: CalibrationStep) -> dict[str, object]:
    return {
        "iteration": s.iteration,
        "intensity_factor": s.intensity_factor,
        "self_match": s.self_match,
        "quality": s.quality,
        "quality_metric": (
            str(s.quality_metric) if s.quality_metric else None
        ),
        "duration_sec": s.duration_sec,
        "note": s.note,
    }


class CalibrateWorker(WorkerBase):
    """Stream CalibrationStep per iteration as a primitive dict."""

    step = pyqtSignal(dict)              # CalibrationStep as dict
    completed = pyqtSignal(object)       # tuned Profile (for caller save)

    def __init__(
        self,
        input_path: Path,
        base_profile: Profile,
        target: CalibrationTarget,
        *,
        encoder_override: str | None = None,
        work_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.base_profile = base_profile
        self.target = target
        self.encoder_override = encoder_override
        self.work_dir = work_dir or Path.home() / ".cache" / "yt_uniquifier" / "calib"

    def run(self) -> None:
        try:
            result = _calibrate(
                input_path=self.input_path,
                base_profile=self.base_profile,
                target=self.target,
                work_dir=self.work_dir,
                encoder_override=self.encoder_override,
                on_step=lambda s: self.step.emit(_step_as_dict(s)),
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        self.completed.emit(result.profile)
        self.finished_ok.emit({
            "factor": result.factor,
            "converged": result.converged,
            "final_self_match": result.final_self_match,
            "final_quality": result.final_quality,
            "final_quality_metric": (
                str(result.final_quality_metric)
                if result.final_quality_metric else None
            ),
            "note": result.note,
            "steps_count": len(result.steps),
        })
