"""Single-file Run screen. Replaces the entire v0.4 GUI flow."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.orchestrator import RunOptions, build_plan
from yt_uniquifier.core.preflight import has_fail, preflight
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.kpi_pills import KpiPills
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.widgets.preflight_panel import PreflightPanel
from yt_uniquifier.gui.widgets.segment_timeline import SegmentTimeline
from yt_uniquifier.gui.workers.probe_worker import ProbeWorker
from yt_uniquifier.gui.workers.run_worker import RunWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class RunScreen(ScreenBase):
    """Single-file uniquification: probe → preflight → run → QA."""

    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.run_worker: RunWorker | None = None
        self.probe_worker: ProbeWorker | None = None
        self.qa_html_path: Path | None = None
        self._build_ui()
        self._refresh_run_button()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Run")
        title.setObjectName("title")
        layout.addWidget(title)

        self.input_picker = FilePickerRow(
            "Input video:", "input",
            "Video (*.mp4 *.mov *.mkv *.webm);;All (*)",
            self.state,
        )
        self.input_picker.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.input_picker)

        self.output_picker = FilePickerRow(
            "Output:", "output",
            "MP4 (*.mp4)",
            self.state,
        )
        self.output_picker.path_changed.connect(self._on_output_changed)
        layout.addWidget(self.output_picker)

        # Profile + encoder
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        opts.addWidget(self.profile_combo, stretch=1)

        self.edit_profile_btn = QPushButton("Edit…")
        self.edit_profile_btn.setEnabled(False)
        self.edit_profile_btn.setToolTip("Coming in v0.5.2 (Profile Editor)")
        opts.addWidget(self.edit_profile_btn)

        opts.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        self.encoder_selector.encoder_changed.connect(self.state.set_encoder_name)
        opts.addWidget(self.encoder_selector, stretch=1)
        layout.addLayout(opts)

        # Preflight panel
        self.preflight_panel = PreflightPanel()
        self.preflight_panel.has_fail.connect(self._on_has_fail)
        layout.addWidget(self.preflight_panel)

        # Controls
        controls = QHBoxLayout()
        self.preflight_btn = QPushButton("Run preflight")
        self.preflight_btn.clicked.connect(self._on_preflight)
        controls.addWidget(self.preflight_btn)

        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setObjectName("run")
        self.run_btn.clicked.connect(self._on_run)
        controls.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        controls.addWidget(self.cancel_btn)

        self.open_qa_btn = QPushButton("Open QA report")
        self.open_qa_btn.setEnabled(False)
        self.open_qa_btn.clicked.connect(self._on_open_qa)
        controls.addWidget(self.open_qa_btn)

        controls.addStretch(1)
        layout.addLayout(controls)

        # Timeline
        self.timeline = SegmentTimeline()
        layout.addWidget(self.timeline)

        # KPI pills (populated only after run finishes)
        self.kpi_pills = KpiPills()
        layout.addWidget(self.kpi_pills)

        # Status + log
        self.status_label = QLabel("Idle.")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        self.log = LogConsole()
        layout.addWidget(self.log, stretch=1)

    # ---- event handlers ----
    def _on_input_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.state.set_input_path(path)
        self._refresh_run_button()
        if path is None:
            return
        # Auto-probe
        if self.probe_worker is not None:
            self.probe_worker.quit()
            self.probe_worker.wait(500)
        self.probe_worker = ProbeWorker(path)
        self.probe_worker.probed.connect(self._on_probed)
        self.probe_worker.failed.connect(
            lambda msg: self.log.log(f"probe: {msg}", "error"),
        )
        self.probe_worker.start()

    def _on_output_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.state.set_output_path(path)
        self._refresh_run_button()

    def _on_probed(self, meta: object) -> None:
        from yt_uniquifier.core.models import SourceMeta
        if not isinstance(meta, SourceMeta):
            return
        if not meta.video:
            self.log.log("probe: no video stream", "error")
            return
        v = meta.video[0]
        self.log.log(
            f"probed: {v.codec} {v.width}×{v.height} @ {v.fps:.2f}fps · "
            f"{meta.duration_sec:.1f}s · HDR={v.color.is_hdr}",
            "info",
        )

    def _on_preflight(self) -> None:
        if self.state.input_path is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
            enc_override = self.encoder_selector.currentData()
            plan = build_plan(self.state.input_path, profile, enc_override)
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Plan error", str(exc))
            return
        findings = preflight(plan.source, plan, plan.encoder)
        self.preflight_panel.set_findings(findings)
        self.log.log(
            f"preflight: {len(findings)} finding(s), "
            f"{'FAIL' if has_fail(findings) else 'PASS'}",
            "info",
        )

    def _on_has_fail(self, has_fail_now: bool) -> None:
        self._refresh_run_button(preflight_fail=has_fail_now)

    def _on_run(self) -> None:
        if self.state.input_path is None or self.state.output_path is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
            enc_override = self.encoder_selector.currentData()
            plan = build_plan(self.state.input_path, profile, enc_override)
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Plan error", str(exc))
            return

        work_dir = Path.home() / ".cache" / "yt_uniquifier" / "work" / plan.plan_hash
        options = RunOptions(
            work_dir=work_dir,
            output=self.state.output_path,
            keep_segments=False,
            enforce_preflight=True,
        )

        # If a previous worker is still being torn down, disconnect its
        # signals before dropping the reference. Otherwise the C++ side
        # may continue to deliver into now-dangling Python slots when
        # the screen is rebuilt.
        if self.run_worker is not None:
            try:
                self.run_worker.disconnect(self)  # type: ignore[arg-type]
            except (TypeError, RuntimeError):
                pass

        self.run_worker = RunWorker(
            plan, options, run_qa=True, fast_qa=False, state=self.state,
        )
        self.run_worker.progress.connect(self._on_progress)
        self.run_worker.log.connect(lambda m: self.log.log(m, "log"))
        self.run_worker.segment_progress.connect(
            lambda idx, status: self.timeline.update_segment(idx, status),
        )
        self.run_worker.finished_ok.connect(self._on_done)
        self.run_worker.failed.connect(self._on_failed)
        self.run_worker.cancelled.connect(self._on_cancelled)

        # Initialise timeline with rough segment estimate.
        n_segments = max(1, int(plan.source.duration_sec / options.target_segment_sec))
        self.timeline.init(n_segments + 1)

        self.qa_html_path = None
        self.open_qa_btn.setEnabled(False)
        self.kpi_pills.clear()
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(
            f"Running on {plan.encoder.name} ({plan.encoder.vendor})…",
        )
        self.log.log(f"--- Run started: plan_hash={plan.plan_hash} ---", "info")
        self.run_worker.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.status_label.setText(message)

    def _on_done(self, output: str, qa_html: str) -> None:
        self.status_label.setText(f"Done: {output}")
        self.timeline.reset()
        self.run_worker = None
        self.cancel_btn.setEnabled(False)
        if qa_html:
            self.qa_html_path = Path(qa_html)
            self.open_qa_btn.setEnabled(True)
            qa_json = self.qa_html_path.with_suffix(".json")
            try:
                qa = json.loads(qa_json.read_text())
                self.kpi_pills.set_qa(qa)
            except (OSError, json.JSONDecodeError):
                pass
        self._refresh_run_button()

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("Failed.")
        self.log.log(f"!!! {message}", "error")
        self.run_worker = None
        self.cancel_btn.setEnabled(False)
        self._refresh_run_button()

    def _on_cancelled(self) -> None:
        """Distinct from _on_failed: user cancellation is not an error."""
        self.status_label.setText("Cancelled.")
        self.log.log("--- Cancelled by user ---", "info")
        self.run_worker = None
        self.cancel_btn.setEnabled(False)
        self._refresh_run_button()

    def _on_cancel(self) -> None:
        if self.run_worker is not None:
            self.run_worker.request_cancel()
            self.status_label.setText("Cancelling…")

    def _on_open_qa(self) -> None:
        if self.qa_html_path and self.qa_html_path.exists():
            webbrowser.open(self.qa_html_path.as_uri())

    def _refresh_run_button(self, *, preflight_fail: bool = False) -> None:
        ready = (
            self.state.input_path is not None
            and self.state.output_path is not None
            and self.run_worker is None
            and not preflight_fail
        )
        self.run_btn.setEnabled(ready)
