"""Off-thread wrapper for build_plan + preflight.

`build_plan` invokes `probe` (ffprobe subprocess) and may invoke
`detect_encoders` on a cold cache (up to ~15 ffmpeg probes). Running
that on the GUI thread froze the event loop for several seconds.

The screen connects to `plan_ready(object, object)` — emits the built
Plan and the list of PreflightFinding — or `failed(str)` on error.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.orchestrator import build_plan
from yt_uniquifier.core.preflight import preflight
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.workers.base import WorkerBase


class PreflightWorker(WorkerBase):
    """Build a Plan + run preflight off the GUI thread."""

    plan_ready = pyqtSignal(object, object)

    def __init__(
        self,
        input_path: Path,
        profile_path: Path,
        encoder_override: str | None,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.profile_path = profile_path
        self.encoder_override = encoder_override

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            profile = load_profile(self.profile_path)
            plan = build_plan(self.input_path, profile, self.encoder_override)
            findings = preflight(plan.source, plan, plan.encoder)
        except YtUniquifierError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.plan_ready.emit(plan, findings)
