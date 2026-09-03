"""Calibrate — bounded intensity search with a live diagnostics chart."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from yt_uniquifier.core.calibration.loop import (
    CalibrationMetric,
    CalibrationTarget,
)
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import dump_profile, load_profile
from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.chart_widget import ChartWidget, Series
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.calibrate_worker import CalibrateWorker

PROFILES_DIR = profiles_dir()


class CalibrateScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.input_path: Path | None = None
        self.worker: CalibrateWorker | None = None
        self.tuned_profile: object | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Calibrate")
        title.setObjectName("title")
        layout.addWidget(title)

        self.input_picker = FilePickerRow(
            "Source video:", "input",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
            self.state,
        )
        self.input_picker.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.input_picker)

        # Base profile
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("Base profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        # Default to cid_aware if present.
        idx = self.profile_combo.findText("cid_aware")
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        mark(self.profile_combo, "Base profile",
             "Starting profile whose existing transforms are scaled during search.")
        row_p.addWidget(self.profile_combo, stretch=1)
        layout.addLayout(row_p)

        # Knobs
        knobs = QHBoxLayout()
        knobs.addWidget(QLabel("Target self-match (max):"))
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(0.05, 0.8)
        self.target_spin.setSingleStep(0.05)
        self.target_spin.setValue(0.2)
        mark(self.target_spin, "Target self-match (max)",
             "Upper bound on fingerprint similarity that calibration aims for.")
        knobs.addWidget(self.target_spin)

        knobs.addWidget(QLabel("Min quality (0..100):"))
        self.quality_spin = QDoubleSpinBox()
        self.quality_spin.setRange(60.0, 100.0)
        self.quality_spin.setSingleStep(1.0)
        self.quality_spin.setValue(88.0)
        mark(self.quality_spin, "Minimum quality",
             "Hard floor on VMAF / SSIM — calibration backs off if quality drops below this.")
        knobs.addWidget(self.quality_spin)

        knobs.addWidget(QLabel("Iterations:"))
        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(1, 15)
        self.iter_spin.setValue(5)
        mark(self.iter_spin, "Iterations",
             "Maximum bounded-search trials before selecting the best measured profile.")
        knobs.addWidget(self.iter_spin)

        knobs.addWidget(QLabel("Test clip (s):"))
        self.clip_spin = QSpinBox()
        self.clip_spin.setRange(10, 600)
        self.clip_spin.setValue(60)
        mark(self.clip_spin, "Test clip duration",
             "Total seconds sampled across source start, middle, and end.")
        knobs.addWidget(self.clip_spin)

        knobs.addWidget(QLabel("Metric:"))
        self.metric_combo = QComboBox()
        self.metric_combo.addItem("chromaprint", "chromaprint")
        self.metric_combo.addItem("sscd", "sscd")
        mark(self.metric_combo, "Similarity metric",
             "chromaprint = v0.5 audio-fingerprint predictor; "
             "sscd = SSCD copy-detection embedding (needs [ml] extra).")
        knobs.addWidget(self.metric_combo)

        knobs.addStretch(1)
        layout.addLayout(knobs)

        # Controls
        controls = QHBoxLayout()
        self.run_btn = QPushButton("▶ &Calibrate")
        self.run_btn.setObjectName("run")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._on_run)
        mark(self.run_btn, "Run calibration",
             "Search intensity against independent similarity and quality constraints.",
             shortcut="Ctrl+R")
        controls.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cance&l")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        mark(self.cancel_btn, "Cancel calibration",
             "Abort the search at the next safe boundary.", shortcut="Esc")
        controls.addWidget(self.cancel_btn)
        self.save_btn = QPushButton("&Save tuned profile as…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        mark(self.save_btn, "Save tuned profile",
             "Persist the calibrated profile to a new YAML file.",
             shortcut="Ctrl+S")
        controls.addWidget(self.save_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        # Iteration progress bar — max is set on _on_run from iter_spin.
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("calib_progress")
        self.progress_bar.setRange(0, self.iter_spin.value())
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Iteration: %v / %m")
        layout.addWidget(self.progress_bar)

        # Chart
        self.chart = ChartWidget()
        self.chart.set_series([
            Series(name="intensity_factor", color="#d18b3b"),
            Series(name="self_match",       color="#a83b3b"),
            Series(name="quality / 100",    color="#3ba85c"),
        ])
        layout.addWidget(self.chart)

        # Log
        self.log = LogConsole()
        layout.addWidget(self.log, stretch=1)

    # ---- handlers ----
    def _on_input_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.input_path = path
        self.run_btn.setEnabled(path is not None and self.worker is None)

    def _on_run(self) -> None:
        if self.input_path is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Profile error", str(exc))
            return

        target = CalibrationTarget(
            max_self_match=self.target_spin.value(),
            min_quality=self.quality_spin.value(),
            max_iterations=self.iter_spin.value(),
            test_clip_sec=float(self.clip_spin.value()),
        )

        self.chart.set_series([
            Series(name="intensity_factor", color="#d18b3b"),
            Series(name="self_match",       color="#a83b3b"),
            Series(name="quality / 100",    color="#3ba85c"),
        ])
        self.log.clear()

        metric_raw = self.metric_combo.currentData()
        metric: CalibrationMetric = (
            "sscd" if metric_raw == "sscd" else "chromaprint"
        )
        self.worker = CalibrateWorker(
            self.input_path, profile, target, metric=metric,
        )
        self.worker.step.connect(self._on_step)
        self.worker.completed.connect(self._on_completed)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.tuned_profile = None
        # Reset iteration bar; max may have changed since UI build.
        self.progress_bar.setRange(0, target.max_iterations)
        self.progress_bar.setValue(0)
        self.log.log(
            f"calibrating {self.input_path.name} → target {target.max_self_match} "
            f"(metric={metric})",
            "info",
        )
        self.worker.start()

    def _on_cancel(self) -> None:
        if self.worker is not None:
            self.worker.request_cancel()

    def _on_step(self, step: dict[str, object]) -> None:
        i_raw = step["iteration"]
        f_raw = step["intensity_factor"]
        s_raw = step["self_match"]
        i = int(i_raw) if isinstance(i_raw, (int, float)) else 0
        # Each completed iteration advances the bar. Iteration indices
        # are 1-based per the calibration loop, so clamp at max.
        self.progress_bar.setValue(min(i, self.progress_bar.maximum()))
        factor = float(f_raw) if isinstance(f_raw, (int, float)) else 0.0
        self_match = float(s_raw) if isinstance(s_raw, (int, float)) else 0.0
        self.chart.add_point("intensity_factor", float(i), factor)
        self.chart.add_point("self_match", float(i), self_match)
        q_raw = step.get("quality")
        quality = float(q_raw) if isinstance(q_raw, (int, float)) else None
        if quality is not None:
            self.chart.add_point("quality / 100", float(i), quality / 100.0)
        q_str = f"{quality:.1f}" if quality is not None else "n/a"
        self.log.log(
            f"iter {i}: factor={factor:.2f} "
            f"self_match={self_match:.3f} quality={q_str}",
            "info",
        )

    def _on_completed(self, profile: object) -> None:
        """Tuned profile arrives separately from the summary dict."""
        self.tuned_profile = profile
        self.save_btn.setEnabled(True)

    def _on_finished(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        factor = payload.get("factor")
        final_sm = payload.get("final_self_match")
        verdict = "✓ converged" if payload.get("converged") else "⚠ best-so-far"
        factor_s = f"{float(factor):.2f}" if isinstance(factor, (int, float)) else "?"
        sm_s = f"{float(final_sm):.3f}" if isinstance(final_sm, (int, float)) else "?"
        self.log.log(f"{verdict} — factor={factor_s} self_match={sm_s}", "info")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self.run_btn.setEnabled(self.input_path is not None)

    def _on_failed(self, msg: str) -> None:
        self.log.log(f"FAILED: {msg}", "error")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self.run_btn.setEnabled(self.input_path is not None)

    def _drop_worker(self) -> None:
        """Join the worker QThread before dropping the Python ref.

        Disconnect signal slots before quit/wait so a late ``step`` emit
        fired between ``quit()`` and ``wait()`` cannot reach the chart
        or log after the screen has visibly "stopped" the run.
        """
        import contextlib

        if self.worker is None:
            return
        # PyQt raises TypeError on disconnect() if a signal has no slots
        # connected — desired end state either way, so suppress.
        for sig in (
            self.worker.step,
            self.worker.completed,
            self.worker.finished_ok,
            self.worker.failed,
        ):
            with contextlib.suppress(TypeError):
                sig.disconnect()
        self.worker.quit()
        self.worker.wait(1000)
        self.worker = None

    def _on_save(self) -> None:
        from yt_uniquifier.core.models import Profile as _Profile
        if not isinstance(self.tuned_profile, _Profile):
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save tuned profile",
            "tuned.yaml",
            "YAML (*.yaml)",
        )
        if path_str:
            try:
                dump_profile(self.tuned_profile, Path(path_str))
                self.log.log(f"saved: {path_str}", "info")
            except Exception as exc:
                QMessageBox.critical(self, "Save failed", str(exc))
