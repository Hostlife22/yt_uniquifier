"""Single-file Run screen. Replaces the entire v0.4 GUI flow."""

from __future__ import annotations

import contextlib
import json
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.orchestrator import RunOptions, build_plan
from yt_uniquifier.core.preflight import has_fail
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.divergence_indicator import DivergenceIndicator
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.kpi_pills import KpiPills
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.widgets.preflight_panel import PreflightPanel
from yt_uniquifier.gui.widgets.segment_timeline import SegmentTimeline
from yt_uniquifier.gui.workers.preflight_worker import PreflightWorker
from yt_uniquifier.gui.workers.probe_worker import ProbeWorker
from yt_uniquifier.gui.workers.run_worker import RunWorker

PROFILES_DIR = profiles_dir()


@dataclass(frozen=True)
class PlanCacheEntry:
    """Cached Plan keyed by the inputs that produced it.

    Replaces a positional 4-tuple. Named fields make the cache hit/miss
    check at _on_run readable and robust to future field additions
    (e.g. a workers override) without index shifts.
    """

    input_path: Path
    profile_path: Path
    encoder: str | None
    plan: Plan

    def matches(
        self,
        input_path: Path,
        profile_path: Path,
        encoder: str | None,
    ) -> bool:
        return (
            self.input_path == input_path
            and self.profile_path == profile_path
            and self.encoder == encoder
        )


class RunScreen(ScreenBase):
    """Single-file uniquification: probe → preflight → run → QA."""

    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        # Forward-declare optional CalibrateWorker so type-checkers see
        # the right `T | None` shape from the first assignment. Imported
        # locally because it pulls Qt + calibration deps that the
        # PreflightWorker tests stub at module load time.
        from yt_uniquifier.gui.workers.calibrate_worker import CalibrateWorker
        self.run_worker: RunWorker | None = None
        self.probe_worker: ProbeWorker | None = None
        self.preflight_worker: PreflightWorker | None = None
        self._tune_worker: CalibrateWorker | None = None
        self._tune_source_path: Path | None = None
        self.qa_html_path: Path | None = None
        # Cached Plan from the most recent preflight, keyed by
        # (input_path, profile_path, encoder). Lets _on_run reuse the
        # Plan that _on_preflight already produced when the user clicked
        # Preflight first — avoids a redundant probe and prevents the
        # two paths from diverging.
        self._plan_cache: PlanCacheEntry | None = None
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
        mark(
            self.profile_combo, "Profile",
            "Choose the transform profile (soft, medium, aggressive, cid_aware, …).",
        )
        opts.addWidget(self.profile_combo, stretch=1)

        self.edit_profile_btn = QPushButton("Edit…")
        self.edit_profile_btn.setEnabled(False)
        self.edit_profile_btn.setToolTip("Coming in v0.5.2 (Profile Editor)")
        mark(self.edit_profile_btn, "Edit profile", "Open the selected profile in the editor.")
        opts.addWidget(self.edit_profile_btn)

        opts.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        self.encoder_selector.encoder_changed.connect(self.state.set_encoder_name)
        opts.addWidget(self.encoder_selector, stretch=1)
        layout.addLayout(opts)

        # Preflight panel
        self.preflight_panel = PreflightPanel(self.state)
        self.preflight_panel.has_fail.connect(self._on_has_fail)
        layout.addWidget(self.preflight_panel)

        # Controls — primary CTAs get keyboard shortcuts so power users
        # never need the mouse. Ctrl+R Run, Esc Cancel, Ctrl+P Preflight.
        controls = QHBoxLayout()
        self.preflight_btn = QPushButton("&Preflight")
        self.preflight_btn.clicked.connect(self._on_preflight)
        mark(
            self.preflight_btn, "Run preflight",
            "Probe the source and warn about encode-blocking issues.",
            shortcut="Ctrl+P",
        )
        controls.addWidget(self.preflight_btn)

        self.run_btn = QPushButton("▶ &Run")
        self.run_btn.setObjectName("run")
        self.run_btn.clicked.connect(self._on_run)
        mark(
            self.run_btn, "Run",
            "Start the uniquification pipeline on the selected input.",
            shortcut="Ctrl+R",
        )
        controls.addWidget(self.run_btn)

        # F7 — Auto-tune the selected profile against the current input,
        # then save the result as <profile>.tuned.yaml and switch the
        # state's profile_path to it.  Disabled until both input and
        # profile are picked.
        self.auto_tune_btn = QPushButton("🎯 Auto-&tune")
        self.auto_tune_btn.clicked.connect(self._on_auto_tune)
        mark(
            self.auto_tune_btn, "Auto-tune profile",
            "Calibrate the selected profile against this input, save the result as "
            "<profile>.tuned.yaml, and switch to it.",
            shortcut="Ctrl+T",
        )
        controls.addWidget(self.auto_tune_btn)

        self.cancel_btn = QPushButton("&Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        mark(
            self.cancel_btn, "Cancel run",
            "Stop the current encode at the next safe boundary.",
            shortcut="Esc",
        )
        controls.addWidget(self.cancel_btn)

        self.open_qa_btn = QPushButton("Open &QA report")
        self.open_qa_btn.setEnabled(False)
        self.open_qa_btn.clicked.connect(self._on_open_qa)
        mark(
            self.open_qa_btn, "Open QA report",
            "Open the HTML report from the most recent run.",
            shortcut="Ctrl+Q",
        )
        controls.addWidget(self.open_qa_btn)

        controls.addStretch(1)
        layout.addLayout(controls)

        # Timeline
        self.timeline = SegmentTimeline()
        layout.addWidget(self.timeline)

        # Live divergence indicator (v0.7 R4 / F2). Hidden until the
        # first divergence_sample arrives, so off-mode runs don't show
        # a row of dashes.
        self.divergence_indicator = DivergenceIndicator(self.state)
        layout.addWidget(self.divergence_indicator)

        # Main overall progress (video segments)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("main_progress")
        self.progress_bar.setRange(0, 1000)  # 0.1% resolution
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Video: %p%")
        layout.addWidget(self.progress_bar)

        # Audio finalize sub-bar (hidden until phase=main_audio fires)
        self.audio_progress_bar = QProgressBar()
        self.audio_progress_bar.setObjectName("audio_progress")
        self.audio_progress_bar.setRange(0, 1000)
        self.audio_progress_bar.setValue(0)
        self.audio_progress_bar.setFormat("Audio: %p%")
        self.audio_progress_bar.setVisible(False)
        layout.addWidget(self.audio_progress_bar)

        # KPI pills (populated only after run finishes)
        self.kpi_pills = KpiPills(self.state)
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
        self._drop_probe_worker()
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
        # Probe done — release the worker so the QThread doesn't sit
        # idle in Python's ref graph until the next input change.
        self._drop_probe_worker()

    def _drop_probe_worker(self) -> None:
        if self.probe_worker is None:
            return
        self.probe_worker.quit()
        self.probe_worker.wait(500)
        self.probe_worker = None

    def _on_preflight(self) -> None:
        if self.state.input_path is None:
            return
        if self.preflight_worker is not None:
            return  # already running
        profile_path = Path(self.profile_combo.currentData())
        enc_override = self.encoder_selector.currentData()
        self.preflight_btn.setEnabled(False)
        self.log.log("preflight: probing source + checking encoder…", "info")
        self.preflight_worker = PreflightWorker(
            self.state.input_path, profile_path, enc_override,
        )
        # Capture (input, profile_path, enc_override) for the plan-ready
        # slot so it can build the cache key even if the user has
        # tweaked the UI selectors while preflight ran.
        cache_key = (self.state.input_path, profile_path, enc_override)
        self.preflight_worker.plan_ready.connect(
            lambda plan, findings: self._on_preflight_ready(plan, findings, cache_key),
        )
        self.preflight_worker.failed.connect(self._on_preflight_failed)
        self.preflight_worker.start()

    def _on_preflight_ready(
        self,
        plan: object,
        findings: object,
        cache_key: tuple[Path, Path, str | None],
    ) -> None:
        if not isinstance(plan, Plan) or not isinstance(findings, list):
            self._drop_preflight_worker()
            self.preflight_btn.setEnabled(True)
            return
        self.preflight_panel.set_findings(findings)
        input_path, profile_path, encoder = cache_key
        self._plan_cache = PlanCacheEntry(
            input_path=input_path,
            profile_path=profile_path,
            encoder=encoder,
            plan=plan,
        )
        self.log.log(
            f"preflight: {len(findings)} finding(s), "
            f"{'FAIL' if has_fail(findings) else 'PASS'}",
            "info",
        )
        self._drop_preflight_worker()
        self.preflight_btn.setEnabled(True)

    def _on_preflight_failed(self, msg: str) -> None:
        self.log.log(f"preflight: {msg}", "error")
        QMessageBox.critical(self, "Plan error", msg)
        self._drop_preflight_worker()
        self.preflight_btn.setEnabled(True)

    def _drop_preflight_worker(self) -> None:
        if self.preflight_worker is None:
            return
        self.preflight_worker.quit()
        self.preflight_worker.wait(500)
        self.preflight_worker = None

    def _on_has_fail(self, has_fail_now: bool) -> None:
        self._refresh_run_button(preflight_fail=has_fail_now)

    def _on_run(self) -> None:
        if self.state.input_path is None or self.state.output_path is None:
            return
        try:
            profile_path = Path(self.profile_combo.currentData())
            enc_override = self.encoder_selector.currentData()
            # Reuse the Plan that Preflight already built when the
            # user's selections have not changed — avoids re-probing
            # the source and guarantees the two paths see the same
            # encoder, plan_hash, and findings.
            if self._plan_cache is not None and self._plan_cache.matches(
                self.state.input_path, profile_path, enc_override,
            ):
                plan = self._plan_cache.plan
            else:
                profile = load_profile(profile_path)
                plan = build_plan(
                    self.state.input_path, profile, enc_override,
                )
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
            # `QObject.disconnect()` with no args removes every signal
            # connection on this object. The PyQt6 typeshed dropped the
            # `disconnect(receiver)` overload; the no-arg form is the
            # supported way to detach all slots before dropping the
            # Python reference.
            with contextlib.suppress(TypeError, RuntimeError):
                self.run_worker.disconnect()

        self.run_worker = RunWorker(
            plan, options, run_qa=True, fast_qa=False, state=self.state,
        )
        self.run_worker.progress.connect(self._on_progress)
        self.run_worker.audio_progress.connect(self._on_audio_progress)
        self.run_worker.log.connect(lambda m: self.log.log(m, "log"))
        self.run_worker.segment_progress.connect(
            lambda idx, status: self.timeline.update_segment(idx, status),
        )
        self.run_worker.divergence_sample.connect(
            self.divergence_indicator.push_sample,
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
        self.divergence_indicator.reset()
        self.progress_bar.setValue(0)
        self.audio_progress_bar.setValue(0)
        self.audio_progress_bar.setVisible(False)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(
            f"Running on {plan.encoder.name} ({plan.encoder.vendor})…",
        )
        self.log.log(f"--- Run started: plan_hash={plan.plan_hash} ---", "info")
        self.run_worker.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.status_label.setText(message)
        self.progress_bar.setValue(int(fraction * 1000))

    def _on_audio_progress(self, fraction: float, label: str) -> None:
        # First audio event reveals the sub-bar; from that point onwards
        # it tracks loudnorm pass progress while the main bar stays
        # pinned to the video-segments total.
        if not self.audio_progress_bar.isVisible():
            self.audio_progress_bar.setVisible(True)
        self.audio_progress_bar.setValue(int(fraction * 1000))
        self.audio_progress_bar.setFormat(f"Audio ({label}): %p%")

    def _on_done(self, output: str, qa_html: str) -> None:
        self.status_label.setText(f"Done: {output}")
        self.timeline.reset()
        self.progress_bar.setValue(1000)
        if self.audio_progress_bar.isVisible():
            self.audio_progress_bar.setValue(1000)
        self.run_worker = None
        self.cancel_btn.setEnabled(False)
        if qa_html:
            self.qa_html_path = Path(qa_html)
            self.open_qa_btn.setEnabled(True)
            qa_json = self.qa_html_path.with_suffix(".json")
            try:
                qa = json.loads(qa_json.read_text())
                self.kpi_pills.set_qa(qa)
            except (OSError, json.JSONDecodeError) as exc:
                # KPI pills are best-effort decoration; the QA HTML
                # report is the authoritative artifact. Log so a missing
                # JSON file is visible during triage instead of a silent
                # empty pill row.
                self.log.log(f"KPI: failed to read {qa_json.name}: {exc}", "log")
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
        # Auto-tune only needs an input + a profile; output isn't required
        # because we're not encoding the final file yet.  Also gated on no
        # in-flight CalibrateWorker so multiple tunes can't stack.
        self.auto_tune_btn.setEnabled(
            self.state.input_path is not None
            and self.profile_combo.currentData() is not None
            and getattr(self, "_tune_worker", None) is None
            and self.run_worker is None,
        )

    # --- F7 Auto-tune ----------------------------------------------------
    def _on_auto_tune(self) -> None:
        """Spawn a CalibrateWorker on (input, current profile).

        Saves the tuned profile next to the original as
        ``<stem>.tuned.yaml`` and switches ``state.profile_path`` to
        it.  Errors surface as a QMessageBox; cancel is wired through
        ``CalibrateWorker.request_cancel`` like other workers.
        """
        from yt_uniquifier.core.calibration.loop import CalibrationTarget
        from yt_uniquifier.core.profile_loader import load_profile
        from yt_uniquifier.gui.workers.calibrate_worker import CalibrateWorker

        if self.state.input_path is None:
            return
        profile_data = self.profile_combo.currentData()
        if not profile_data:
            return
        try:
            profile = load_profile(Path(profile_data))
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Profile error", str(exc))
            return

        # Conservative defaults — same as the Calibrate screen's
        # starting knob positions.  Users who want different knobs
        # use the dedicated Calibrate screen.
        target = CalibrationTarget(
            max_self_match=0.2,
            min_quality=88.0,
            max_iterations=5,
            test_clip_sec=60.0,
        )

        worker = CalibrateWorker(self.state.input_path, profile, target)
        self._tune_worker = worker
        self._tune_source_path = Path(profile_data)
        worker.failed.connect(self._on_auto_tune_failed)
        worker.completed.connect(self._on_auto_tune_completed)
        worker.finished_ok.connect(self._on_auto_tune_finished)
        self.auto_tune_btn.setEnabled(False)
        self.status_label.setText("Auto-tuning profile…")
        self.log.log(
            f"--- Auto-tune started on {self.state.input_path.name} "
            f"with {Path(profile_data).stem} ---", "info",
        )
        worker.start()

    def _on_auto_tune_completed(self, tuned_profile: object) -> None:
        """Persist the tuned profile and switch state.profile_path to it."""
        from yt_uniquifier.core.models import Profile as _Profile
        from yt_uniquifier.core.profile_loader import dump_profile

        if not isinstance(tuned_profile, _Profile):
            return
        source = getattr(self, "_tune_source_path", None)
        if source is None:
            return
        tuned_path = source.with_name(f"{source.stem}.tuned.yaml")
        try:
            dump_profile(tuned_profile, tuned_path)
        except Exception as exc:  # noqa: BLE001 — surface dump failures to user
            QMessageBox.critical(self, "Save tuned profile failed", str(exc))
            return
        # Reload the combo so the new file appears, then select it.
        self._reload_profile_combo()
        idx = self.profile_combo.findData(str(tuned_path))
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.state.set_profile_path(tuned_path)
        self.log.log(f"Auto-tune saved tuned profile: {tuned_path}", "info")
        self.status_label.setText(f"Tuned profile saved: {tuned_path.name}")

    def _on_auto_tune_failed(self, message: str) -> None:
        self.log.log(f"Auto-tune failed: {message}", "error")
        self.status_label.setText("Auto-tune failed.")
        QMessageBox.critical(self, "Auto-tune failed", message)

    def _on_auto_tune_finished(self, _payload: object) -> None:
        worker = getattr(self, "_tune_worker", None)
        if worker is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                worker.disconnect()
            worker.quit()
            worker.wait(2000)
        self._tune_worker = None
        self._refresh_run_button()

    def _reload_profile_combo(self) -> None:
        """Repopulate the profile dropdown so a newly-saved tuned file shows up."""
        prev = self.profile_combo.currentData()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        if prev:
            idx = self.profile_combo.findData(prev)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
