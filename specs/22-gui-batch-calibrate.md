# Spec 22 — Batch + Calibrate screens (v0.5.1)

> **Phase 22 (v0.5.1)** · 2 days · **Deps:** v0.5.0

## Context

Spec 21 shipped the app shell + Run screen. Two more screens that
directly map to existing CLI commands: `yt-uniq batch` and
`yt-uniq calibrate`. Both reuse the patterns from Spec 21 (WorkerBase,
AppState, widgets) and add minimal new infrastructure:

- **Batch:** iterates `core.orchestrator.run_full` over a directory of
  files. Visual: per-file table with status, progress, output path.
- **Calibrate:** wraps `core.calibration.loop.calibrate(... on_step=...)`.
  Visual: live 3-line convergence chart (intensity_factor, self_match,
  quality per iteration).

Adds one new widget (`ChartWidget`) and two new workers
(`BatchWorker`, `CalibrateWorker`).

## Goal

After v0.5.1:

- **Batch screen** opens via sidebar entry; full flow: select input dir
  → file preview table → output dir → profile + encoder + glob pattern
  → Run → per-file progress with cancel.
- **Calibrate screen** opens via sidebar entry; full flow: select source
  + base profile → target / min-quality sliders + iterations spinner →
  Calibrate → live convergence chart + step log → "Save tuned profile
  as…" button.
- Sidebar placeholder text removed from these two entries.
- Tag: `v0.5.1`.

## Scope

**In:**
- `BatchWorker` — iterates files, runs `run_full` per file, emits
  per-file signals.
- `CalibrateWorker` — wraps `calibrate()` with `on_step` callback
  forwarded as `step(dict)` signal.
- `Batch` screen.
- `Calibrate` screen.
- `ChartWidget` — minimal line-chart widget (QtCharts when available,
  QPainter fallback otherwise).
- Tests for both workers + chart widget smoke.

**Not in:**
- Re-using `BatchWorker` for the Validation wizard (Spec 24 will reuse
  it independently; no coordination needed).
- Profile tweaking after calibrate — `Calibrate` screen writes a new
  YAML, doesn't open Profile Editor (lands in Spec 23).
- Per-segment chart in Batch (too noisy; overall per-file progress
  bar enough).

## Workitem 1 — `BatchWorker`

**File:** `src/yt_uniquifier/gui/workers/batch_worker.py` (new)

```python
"""Iterate orchestrator.run_full over a directory of files."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.runner import RunEvent
from yt_uniquifier.gui.workers.base import WorkerBase


class BatchWorker(WorkerBase):
    """Process every matched file under a directory.

    Per-file signals so the GUI table can update independently of
    overall progress. `continue_on_error` decides whether to stop or
    skip-and-continue on per-file failure.
    """

    file_started = pyqtSignal(str)              # path
    file_done = pyqtSignal(str, str)            # path, output_path
    file_failed = pyqtSignal(str, str)          # path, error_message
    file_progress = pyqtSignal(str, float)      # path, fraction [0..1]

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        profile: Profile,
        encoder_override: str | None,
        *,
        glob_pattern: str = "*.mp4",
        continue_on_error: bool = True,
        work_dir_root: Path | None = None,
        run_qa: bool = True,
        fast_qa: bool = True,
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.profile = profile
        self.encoder_override = encoder_override
        self.glob_pattern = glob_pattern
        self.continue_on_error = continue_on_error
        self.work_dir_root = work_dir_root or (Path.home() / ".cache" / "yt_uniquifier" / "batch")
        self.run_qa = run_qa
        self.fast_qa = fast_qa

    def run(self) -> None:
        files = sorted(self.input_dir.glob(self.glob_pattern))
        if not files:
            self.failed.emit(f"no files matched {self.glob_pattern} in {self.input_dir}")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []
        total = len(files)
        for i, src in enumerate(files):
            if self.cancel_token.is_cancelled():
                self.log.emit(f"cancelled after {i}/{total}")
                break

            out = self.output_dir / f"{src.stem}.uniq.mp4"
            self.file_started.emit(str(src))
            try:
                plan = build_plan(src, self.profile, self.encoder_override)
                opts = RunOptions(
                    work_dir=self.work_dir_root / plan.plan_hash,
                    output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                )
                run_full(
                    plan, opts,
                    on_event=lambda ev, p=str(src): self._on_file_event(p, ev),
                    cancel_token=self.cancel_token,
                )
                self.file_done.emit(str(src), str(out))
                results.append({"path": str(src), "status": "done", "output": str(out)})
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.file_failed.emit(str(src), msg)
                results.append({"path": str(src), "status": "failed", "error": msg})
                if not self.continue_on_error:
                    self.failed.emit(f"stopped at {src.name}: {msg}")
                    return

            self.progress.emit((i + 1) / total, f"{i + 1} / {total} files")

        self.finished_ok.emit({"files_processed": len(results), "results": results})

    def _on_file_event(self, path: str, ev: RunEvent) -> None:
        if ev.kind != "progress":
            return
        # Convert per-file segment progress to a 0..1 fraction for the row.
        out_us = ev.payload.get("out_time_us")
        if isinstance(out_us, str):
            try:
                # Per-file total unknown without re-probe; emit fractional update.
                self.file_progress.emit(path, min(int(out_us) / 1e9, 1.0))
            except ValueError:
                pass
```

**Tests:** `tests/unit/test_gui_batch_worker.py`
- `test_batch_worker_no_files_emits_failed` — empty dir → failed signal
- `test_batch_worker_happy_path_3_files` — mock `run_full` → 3 file_started + 3 file_done + finished_ok
- `test_batch_worker_continue_on_error` — one file raises → file_failed emitted, processing continues
- `test_batch_worker_stop_on_error` — `continue_on_error=False` → failed signal on first error
- `test_batch_worker_cancel_midway` — cancel_token.cancel() after file 1 → no more files processed

## Workitem 2 — `CalibrateWorker`

**File:** `src/yt_uniquifier/gui/workers/calibrate_worker.py` (new)

```python
"""Wrap core.calibration.loop.calibrate for the GUI."""

from __future__ import annotations
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget, calibrate as _calibrate,
)
from yt_uniquifier.core.models import Profile
from yt_uniquifier.gui.workers.base import WorkerBase


class CalibrateWorker(WorkerBase):
    """Stream CalibrationStep per iteration as a primitive dict."""

    step = pyqtSignal(dict)                    # CalibrationStep as dict

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
        self.finished_ok.emit({
            "factor": result.factor,
            "converged": result.converged,
            "final_self_match": result.final_self_match,
            "final_quality": result.final_quality,
            "final_quality_metric": result.final_quality_metric.name
                                     if result.final_quality_metric else None,
            "note": result.note,
            "steps_count": len(result.steps),
            "tuned_profile": result.profile,    # full Profile, GUI saves it
        })


def _step_as_dict(s) -> dict:
    return {
        "iteration": s.iteration,
        "intensity_factor": s.intensity_factor,
        "self_match": s.self_match,
        "quality": s.quality,
        "quality_metric": s.quality_metric.name if s.quality_metric else None,
        "duration_sec": s.duration_sec,
        "note": s.note,
    }
```

**Tests:** `tests/unit/test_gui_calibrate_worker.py`
- `test_calibrate_worker_emits_step_per_iteration` — mock `_calibrate` calling on_step 3× → 3 step signals
- `test_calibrate_worker_finished_payload` — finished_ok carries `factor`, `converged`, `tuned_profile`
- `test_calibrate_worker_failed_on_exception` — mock raises → failed signal

## Workitem 3 — `ChartWidget`

**File:** `src/yt_uniquifier/gui/widgets/chart_widget.py` (new)

```python
"""Minimal multi-series line chart. QtCharts when available, QPainter fallback."""

from __future__ import annotations
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

try:
    from PyQt6.QtCharts import (
        QChart, QChartView, QLineSeries, QValueAxis,
    )
    HAS_QTCHARTS = True
except ImportError:
    HAS_QTCHARTS = False


@dataclass
class Series:
    name: str
    color: str                    # CSS hex e.g. "#3b6ea8"
    points: list[tuple[float, float]]   # [(x, y), …]


class ChartWidget(QWidget):
    """Render N line series. Backend depends on QtCharts availability.

    Public API:
      set_series(list[Series]) — replaces all series, re-renders.
      add_point(series_name, x, y) — append a point, re-renders.
    """

    def __init__(self) -> None:
        super().__init__()
        self._series: list[Series] = []
        self.setMinimumHeight(220)
        if HAS_QTCHARTS:
            self._build_qtcharts()
        else:
            self._build_fallback()

    def set_series(self, series: list[Series]) -> None:
        self._series = list(series)
        self._refresh()

    def add_point(self, name: str, x: float, y: float) -> None:
        for s in self._series:
            if s.name == name:
                s.points.append((x, y))
                self._refresh()
                return
        self._series.append(Series(name=name, color="#3b6ea8", points=[(x, y)]))
        self._refresh()

    def _build_qtcharts(self) -> None:
        from PyQt6.QtWidgets import QVBoxLayout
        self._chart = QChart()
        self._chart.legend().setVisible(True)
        self._view = QChartView(self._chart)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    def _build_fallback(self) -> None:
        pass  # QPainter-based, draws in paintEvent

    def _refresh(self) -> None:
        if HAS_QTCHARTS:
            self._chart.removeAllSeries()
            for s in self._series:
                line = QLineSeries()
                line.setName(s.name)
                pen = QPen(QColor(s.color))
                pen.setWidth(2)
                line.setPen(pen)
                for x, y in s.points:
                    line.append(x, y)
                self._chart.addSeries(line)
            self._chart.createDefaultAxes()
        else:
            self.update()  # triggers paintEvent

    def paintEvent(self, event):
        if HAS_QTCHARTS:
            return  # QChartView handles paint
        # QPainter fallback for environments without QtCharts.
        super().paintEvent(event)
        # ... simple line-plot implementation:
        # - find min/max x and y across all series
        # - draw each series as connected lines normalized to widget rect
        # - draw legend (small color squares + names)
        # 30-50 lines of QPainter primitives — straightforward but skip for spec brevity.
```

**Tests:** `tests/unit/test_gui_chart_widget.py`
- `test_chart_widget_renders_without_qtcharts` — `HAS_QTCHARTS = False` patch → widget creates without errors, `paintEvent` runs
- `test_chart_widget_add_point_appends` — add 3 points to one series → series has 3 points
- `test_chart_widget_set_series_replaces` — set 2 series then set 1 → only 1 remains

## Workitem 4 — Batch screen

**File:** `src/yt_uniquifier/gui/screens/batch.py` (new)

```python
"""Batch directory processing."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.workers.batch_worker import BatchWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class BatchScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.input_dir: Path | None = None
        self.output_dir: Path | None = None
        self.worker: BatchWorker | None = None
        self._row_index: dict[str, int] = {}     # source path → table row idx
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Input dir
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Input directory:"))
        self.input_label = QLabel("(none)")
        self.input_label.setObjectName("path")
        row1.addWidget(self.input_label, stretch=1)
        self.input_btn = QPushButton("Browse…")
        self.input_btn.clicked.connect(self._pick_input)
        row1.addWidget(self.input_btn)
        layout.addLayout(row1)

        # Output dir + glob pattern
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Output directory:"))
        self.output_label = QLabel("(none)")
        self.output_label.setObjectName("path")
        row2.addWidget(self.output_label, stretch=1)
        self.output_btn = QPushButton("Browse…")
        self.output_btn.clicked.connect(self._pick_output)
        row2.addWidget(self.output_btn)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Pattern:"))
        self.pattern_edit = QLineEdit("*.mp4")
        self.pattern_edit.textChanged.connect(self._refresh_preview)
        row3.addWidget(self.pattern_edit)
        layout.addLayout(row3)

        # Profile + encoder + continue-on-error
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        row4.addWidget(self.profile_combo, stretch=1)
        row4.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        row4.addWidget(self.encoder_selector, stretch=1)
        self.continue_check = QCheckBox("Continue on error")
        self.continue_check.setChecked(True)
        row4.addWidget(self.continue_check)
        layout.addLayout(row4)

        # File table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["File", "Status", "Progress", "Output", "Notes"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        # Run/cancel
        controls = QHBoxLayout()
        self.run_btn = QPushButton("▶ Run batch")
        self.run_btn.setObjectName("run")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._on_run)
        controls.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        controls.addWidget(self.cancel_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

    # ... _pick_input / _pick_output / _refresh_preview / _on_run / _on_cancel /
    # ... event handlers for worker signals (file_started → set row status,
    # ... file_done → set row status + output, file_failed → red row, etc.)
```

## Workitem 5 — Calibrate screen

**File:** `src/yt_uniquifier/gui/screens/calibrate.py` (new)

```python
"""Calibrate workflow with live convergence chart."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QVBoxLayout,
)

from yt_uniquifier.core.calibration.loop import CalibrationTarget
from yt_uniquifier.core.profile_loader import dump_profile, load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.chart_widget import ChartWidget, Series
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.calibrate_worker import CalibrateWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class CalibrateScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.input_path: Path | None = None
        self.worker: CalibrateWorker | None = None
        self.tuned_profile = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Source
        self.input_picker = FilePickerRow("Source video:", "input",
                                          "*.mp4 *.mov *.mkv", self.state)
        self.input_picker.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.input_picker)

        # Base profile
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("Base profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        row_p.addWidget(self.profile_combo, stretch=1)
        layout.addLayout(row_p)

        # Sliders
        sliders = QHBoxLayout()
        sliders.addWidget(QLabel("Target self-match (max):"))
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(0.05, 0.8)
        self.target_spin.setSingleStep(0.05)
        self.target_spin.setValue(0.2)
        sliders.addWidget(self.target_spin)

        sliders.addWidget(QLabel("Min quality (0..100):"))
        self.quality_spin = QDoubleSpinBox()
        self.quality_spin.setRange(60.0, 100.0)
        self.quality_spin.setSingleStep(1.0)
        self.quality_spin.setValue(88.0)
        sliders.addWidget(self.quality_spin)

        sliders.addWidget(QLabel("Iterations:"))
        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(1, 15)
        self.iter_spin.setValue(5)
        sliders.addWidget(self.iter_spin)
        layout.addLayout(sliders)

        # Controls
        controls = QHBoxLayout()
        self.run_btn = QPushButton("▶ Calibrate")
        self.run_btn.setObjectName("run")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._on_run)
        controls.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        controls.addWidget(self.cancel_btn)
        self.save_btn = QPushButton("Save tuned profile as…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        controls.addWidget(self.save_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        # Convergence chart
        self.chart = ChartWidget()
        self.chart.set_series([
            Series(name="intensity_factor", color="#d18b3b", points=[]),
            Series(name="self_match",       color="#a83b3b", points=[]),
            Series(name="quality / 100",    color="#3ba85c", points=[]),
        ])
        layout.addWidget(self.chart)

        # Step log
        self.log = LogConsole()
        layout.addWidget(self.log, stretch=1)

    def _on_run(self) -> None:
        profile = load_profile(Path(self.profile_combo.currentData()))
        target = CalibrationTarget(
            max_self_match=self.target_spin.value(),
            min_quality=self.quality_spin.value(),
            max_iterations=self.iter_spin.value(),
        )
        self.worker = CalibrateWorker(self.input_path, profile, target)
        self.worker.step.connect(self._on_step)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(lambda msg: self.log.log(f"FAILED: {msg}", "error"))
        self.worker.start()
        # ... toggle button states

    def _on_step(self, step: dict) -> None:
        i = step["iteration"]
        self.chart.add_point("intensity_factor", i, step["intensity_factor"])
        self.chart.add_point("self_match", i, step["self_match"])
        quality = step.get("quality")
        if quality is not None:
            self.chart.add_point("quality / 100", i, quality / 100.0)
        self.log.log(
            f"iter {i}: factor={step['intensity_factor']:.2f} "
            f"self_match={step['self_match']:.3f} "
            f"quality={quality:.1f if quality is not None else 'n/a'}",
            "info",
        )

    def _on_done(self, payload: dict) -> None:
        self.tuned_profile = payload["tuned_profile"]
        self.save_btn.setEnabled(True)
        verdict = "✓ converged" if payload["converged"] else "⚠ did not converge"
        self.log.log(
            f"{verdict} — factor={payload['factor']:.2f}, "
            f"self_match={payload['final_self_match']:.3f}", "info",
        )

    def _on_save(self) -> None:
        if self.tuned_profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save tuned profile",
                                              "tuned.yaml", "YAML (*.yaml)")
        if path:
            dump_profile(self.tuned_profile, Path(path))
            self.log.log(f"saved: {path}", "info")
```

## Workitem 6 — Sidebar registration

**File:** `src/yt_uniquifier/gui/app_pyqt.py` — update `SIDEBAR_ITEMS`:

```python
from yt_uniquifier.gui.screens.batch import BatchScreen
from yt_uniquifier.gui.screens.calibrate import CalibrateScreen

SIDEBAR_ITEMS = [
    ("Run",            RunScreen),
    ("Batch",          BatchScreen),         # was: PlaceholderScreen
    ("Calibrate",      CalibrateScreen),     # was: PlaceholderScreen
    # ... rest remain placeholders for 23–25
]
```

## Acceptance

```bash
# 1. Launch.
yt-uniq-gui

# 2. Click "Batch" in sidebar → screen loads (not placeholder).
# 3. Pick input dir with 3 mp4s → table preview shows 3 rows.
# 4. Pick output dir → enable Run.
# 5. Click Run → 3 rows update status: pending → running → done.
# 6. Cancel mid-second-file → file 1 done, file 2 cancelled, file 3 not started.

# 7. Click "Calibrate" in sidebar.
# 8. Drop a short clip → select cid_aware → defaults → Calibrate.
# 9. Chart populates 1 point per iteration; convergence visible after ~3 iters.
# 10. Click "Save tuned profile as…" → write tuned.yaml.

# 11. Tests + lint.
pytest -q tests/unit/test_gui_batch_* tests/unit/test_gui_calibrate_* tests/unit/test_gui_chart_*
ruff check . && mypy src/yt_uniquifier/gui
```

## Tests

| Уровень | Файл | Кол-во |
|---|---|---|
| Unit | `test_gui_batch_worker.py` | 5 |
| Unit | `test_gui_calibrate_worker.py` | 3 |
| Unit | `test_gui_chart_widget.py` | 3 |
| **Total** | | **~11 new** |

Screen-level smoke tests skipped — covered by the v0.5.4 full-launch
smoke test.

## Risks

| Риск | Митигация |
|---|---|
| `PyQt6-Charts` не установлен на user's system | `ChartWidget` graceful fallback на QPainter — verified by `test_chart_widget_renders_without_qtcharts` |
| BatchWorker long-running, blocking cancel | `cancel_token.is_cancelled()` checked between files + propagated to `run_full` per-file |
| CalibrateWorker fails on first iteration → user thinks UI hung | failed signal connected to log + restores button state; failed run leaves Save button disabled |
| Batch table grows beyond visible area for 100+ files | `QAbstractItemView.ScrollMode.ScrollPerPixel` + vertical scrollbar — default Qt behavior, no extra work |
| QPainter fallback chart looks crude on Retina display | Acceptable — chart is informational; users on Mac usually have QtCharts available anyway |

## Hand-off

После v0.5.1:

- Batch and Calibrate screens функциональны.
- 5 placeholders остались (QA Viewer, Profile Editor, History, Corpus,
  Queue, Validation, Settings — 7 actually).
- `ChartWidget` reusable для других potential charts (history KPI
  trends в Spec 23, validation correlation в Spec 24).
- ~11 новых тестов.

Tag: `v0.5.1`.

## Effort

| Item | Time |
|---|---|
| 1. `BatchWorker` + 5 tests | 2 hours |
| 2. `CalibrateWorker` + 3 tests | 1 hour |
| 3. `ChartWidget` + 3 tests (with QPainter fallback) | 2.5 hours |
| 4. Batch screen | 2 hours |
| 5. Calibrate screen | 2 hours |
| 6. Sidebar wiring + smoke check + commit + tag | 30 min |
| **Total** | **~10 hours / 2 working days** |
