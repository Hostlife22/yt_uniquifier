# Spec 23 — QA Viewer + Profile Editor + History (v0.5.2)

> **Phase 23 (v0.5.2)** · 3 days · **Deps:** v0.5.0 (foundation) + v0.5.1 (batch/calibrate)

## Context

Three independent screens, all reachable from earlier screens:

- **QA Viewer** — opens any `*.qa.html` (from Run, Batch, Calibrate's
  test-clip output) or computes a new QA on (input, output) pair.
  `QWebEngineView` for embedded rendering with graceful fallback to
  "Open in browser" if WebEngine is missing.
- **Profile Editor** — replaces the disabled "Edit…" button in Run
  screen. Pydantic-validated forms with YAML preview. Save / Save as /
  Duplicate. New profiles appear in profile combos elsewhere.
- **History** — persisted run list. Click row → repopulate Run screen
  inputs OR open the output's QA report directly.

All three reuse existing widgets and `WorkerBase`. New addition:
`QaWorker` (background QA computation) and `HistoryEntry` integration
with `RunWorker`/`BatchWorker`/`CalibrateWorker.finished_ok`.

## Goal

After v0.5.2:

- **QA Viewer screen** opens via sidebar or via "View QA report" button
  in other screens. Two modes: load existing `.qa.html` or compute new.
- **Profile Editor screen** opens via sidebar OR via Run screen's
  "Edit…" button. Edits any profile (including shipped ones — saves to
  a copy by default unless explicit overwrite). New custom profiles
  appear in profile combos across the app.
- **History screen** opens via sidebar. Shows last 100 runs with
  filterable columns. Row actions: Open output, Open QA report, Re-run.
- `RunWorker.finished_ok` and `BatchWorker.file_done` now push
  `HistoryEntry` via `AppState.push_history`.
- 4 sidebar placeholders removed (QA Viewer, Profile Editor, History).
- Tag: `v0.5.2`.

## Scope

**In:**
- `QaWorker` — wraps `build_report` + `render_html` + `write_json`.
- `QA Viewer` screen (with `QWebEngineView` + fallback).
- `Profile Editor` screen — form-based per-transform parameters with
  pydantic validation.
- `History` screen.
- Hook `RunWorker.finished_ok` / `BatchWorker.file_done` /
  `CalibrateWorker.finished_ok` → `AppState.push_history`.
- "Edit profile" button on Run screen now wired to Profile Editor.

**Not in:**
- KPI trend chart in History (could be a v0.6 addition; for now table only).
- Diff view in Profile Editor (compare two profiles side by side) —
  defer until users request it.
- Save profile as preset that's user-shareable (just YAML on disk; user
  can copy/paste).
- Hot-reload profile in active run (changing profile during a run is
  out of scope; profile is bound at `build_plan` time).

## Workitem 1 — `QaWorker`

**File:** `src/yt_uniquifier/gui/workers/qa_worker.py` (new)

```python
"""Standalone QA computation on an (input, output) pair."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.gui.workers.base import WorkerBase


class QaWorker(WorkerBase):
    """Compute QA report; emit paths to written .qa.json + .qa.html."""

    qa_ready = pyqtSignal(str, str)            # qa_json_path, qa_html_path

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        *,
        plan: Plan | None = None,             # used for rendering plan section
        run_vmaf: bool = True,
        run_ssim: bool = True,
        run_audio_fp: bool = True,
        predict_cid: bool = True,
        vs_corpus: bool = False,
        samples: int = 120,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.plan = plan
        self.run_vmaf = run_vmaf
        self.run_ssim = run_ssim
        self.run_audio_fp = run_audio_fp
        self.predict_cid = predict_cid
        self.vs_corpus = vs_corpus
        self.samples = samples

    def run(self) -> None:
        try:
            corpus = None
            if self.vs_corpus:
                from yt_uniquifier.core.qa.corpus import Corpus
                corpus = Corpus()  # default root
            report = build_report(
                self.input_path, self.output_path,
                samples=self.samples,
                run_vmaf=self.run_vmaf,
                run_ssim=self.run_ssim,
                run_audio_fp=self.run_audio_fp,
                predict_cid=self.predict_cid,
                vs_corpus=corpus,
                progress=lambda phase, frac: self.progress.emit(
                    frac, f"{phase} {int(frac * 100)}%"
                ),
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        qa_json = self.output_path.with_suffix(self.output_path.suffix + ".qa.json")
        qa_html = self.output_path.with_suffix(self.output_path.suffix + ".qa.html")
        try:
            write_json(report, qa_json)
            render_html(report, self.plan, qa_html)
        except Exception as exc:
            self.failed.emit(f"render: {exc}")
            return

        self.qa_ready.emit(str(qa_json), str(qa_html))
        self.finished_ok.emit({"qa_json": str(qa_json), "qa_html": str(qa_html)})
```

**Tests:** `tests/unit/test_gui_qa_worker.py`
- `test_qa_worker_happy_path` — mock `build_report` → qa_ready signal carries paths
- `test_qa_worker_failed_on_build_error` — `build_report` raises → failed signal
- `test_qa_worker_progress_emitted` — phases produce progress signal updates

## Workitem 2 — QA Viewer screen

**File:** `src/yt_uniquifier/gui/screens/qa_viewer.py` (new)

```python
"""View existing *.qa.html or compute new QA on (input, output) pair."""

from __future__ import annotations
import webbrowser
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.qa_worker import QaWorker


class QaViewerScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.qa_html_path: Path | None = None
        self.worker: QaWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Mode tabs: Open existing | Compute new
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_open_tab(), "Open existing")
        self.tabs.addTab(self._build_compute_tab(), "Compute new")
        layout.addWidget(self.tabs)

        # Viewer (shared by both modes)
        if HAS_WEBENGINE:
            self.viewer = QWebEngineView()
            layout.addWidget(self.viewer, stretch=1)
        else:
            self.viewer = QLabel(
                "<i>PyQt6-WebEngine not installed. Install with:</i><br>"
                "<code>pip install 'yt-uniquifier[gui]'</code><br><br>"
                "Use 'Open in browser' below as a fallback."
            )
            self.viewer.setWordWrap(True)
            layout.addWidget(self.viewer, stretch=1)

        # Footer actions
        footer = QHBoxLayout()
        self.open_browser_btn = QPushButton("Open in browser")
        self.open_browser_btn.setEnabled(False)
        self.open_browser_btn.clicked.connect(self._open_in_browser)
        footer.addWidget(self.open_browser_btn)
        footer.addStretch(1)
        layout.addLayout(footer)

    def _build_open_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("QA report (.qa.html):"))
        self.existing_label = QLabel("(none)")
        self.existing_label.setObjectName("path")
        row.addWidget(self.existing_label, stretch=1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._pick_existing)
        row.addWidget(btn)
        l.addLayout(row)
        return w

    def _build_compute_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        self.input_picker = FilePickerRow("Input video:", "input",
                                          "*.mp4 *.mov *.mkv", self.state)
        self.output_picker = FilePickerRow("Output video:", "input",
                                           "*.mp4 *.mov *.mkv", self.state)
        l.addWidget(self.input_picker)
        l.addWidget(self.output_picker)
        # Options: fast_qa, vs_corpus
        self.compute_btn = QPushButton("Compute QA")
        self.compute_btn.clicked.connect(self._on_compute)
        l.addWidget(self.compute_btn)
        self.compute_log = LogConsole()
        l.addWidget(self.compute_log)
        return w

    def _pick_existing(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "QA HTML report", "", "QA report (*.qa.html);;HTML (*.html)"
        )
        if path:
            self._load_html(Path(path))

    def _load_html(self, path: Path) -> None:
        self.qa_html_path = path
        self.existing_label.setText(str(path))
        self.open_browser_btn.setEnabled(True)
        if HAS_WEBENGINE:
            self.viewer.load(QUrl.fromLocalFile(str(path)))

    def _on_compute(self) -> None:
        input_p = self.state._input_path
        output_p = self.state._output_path
        if input_p is None or output_p is None:
            QMessageBox.warning(self, "Inputs missing",
                                "Select both input and output paths.")
            return
        self.worker = QaWorker(input_p, output_p)
        self.worker.progress.connect(
            lambda f, m: self.compute_log.log(f"[{int(f * 100)}%] {m}", "info")
        )
        self.worker.qa_ready.connect(lambda j, h: self._load_html(Path(h)))
        self.worker.failed.connect(lambda msg: self.compute_log.log(f"FAILED: {msg}", "error"))
        self.worker.start()

    def _open_in_browser(self) -> None:
        if self.qa_html_path:
            webbrowser.open(self.qa_html_path.as_uri())
```

**Tests:** `tests/unit/test_gui_qa_viewer.py`
- `test_qa_viewer_no_webengine_shows_fallback_label` — patch `HAS_WEBENGINE=False` → label visible
- `test_qa_viewer_load_html_enables_browser_btn` — `_load_html(path)` enables button
- `test_qa_viewer_compute_requires_both_paths` — missing path → QMessageBox warning

## Workitem 3 — Profile Editor screen

**File:** `src/yt_uniquifier/gui/screens/profile_editor.py` (new)

Form-based editor. Left side: transforms tree (toggle enabled
checkboxes, expand to show params). Right side: parameter form for
selected transform (auto-generated from pydantic schema). Top-level
fields (audio_tracks, keep_hdr, seed_strategy, target_loudness_lufs) at
the bottom. "Preview YAML" tab shows `yaml.dump(profile.model_dump())`.

```python
"""Inline YAML profile editor with pydantic validation."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from pydantic import BaseModel, ValidationError
import yaml

from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.profile_loader import dump_profile, load_profile
from yt_uniquifier.core.transforms import all_ids, get
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class ProfileEditorScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.current_profile: Profile | None = None
        self.current_path: Path | None = None
        self._field_widgets: dict[str, dict[str, QWidget]] = {}  # tid → {param: widget}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Top bar: open / save / save as / new
        bar = QHBoxLayout()
        self.profile_combo = QComboBox()
        self._reload_profile_combo()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_select)
        bar.addWidget(QLabel("Profile:"))
        bar.addWidget(self.profile_combo, stretch=1)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._on_save)
        bar.addWidget(self.save_btn)
        self.save_as_btn = QPushButton("Save as…")
        self.save_as_btn.clicked.connect(self._on_save_as)
        bar.addWidget(self.save_as_btn)
        self.new_btn = QPushButton("New from medium")
        self.new_btn.clicked.connect(self._on_new)
        bar.addWidget(self.new_btn)
        layout.addLayout(bar)

        # Body: splitter — transforms tree (left) | param form (right)
        splitter = QSplitter()
        # Left: tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Transform", "Enabled"])
        self.tree.itemClicked.connect(self._on_transform_select)
        self.tree.itemChanged.connect(self._on_transform_check_changed)
        splitter.addWidget(self.tree)
        # Right: stacked param forms
        self.param_stack = QStackedWidget()
        splitter.addWidget(self.param_stack)
        splitter.setSizes([300, 600])
        layout.addWidget(splitter, stretch=1)

        # Top-level fields box
        top_fields = QGroupBox("Profile-level settings")
        form = QFormLayout(top_fields)
        self.audio_tracks_combo = QComboBox()
        self.audio_tracks_combo.addItems(["first", "all"])
        form.addRow("audio_tracks", self.audio_tracks_combo)
        self.keep_hdr_check = QCheckBox()
        form.addRow("keep_hdr", self.keep_hdr_check)
        self.seed_strategy_combo = QComboBox()
        self.seed_strategy_combo.addItems(["fixed", "per_run", "per_file", "divergent"])
        form.addRow("seed_strategy", self.seed_strategy_combo)
        self.target_loudness_spin = QDoubleSpinBox()
        self.target_loudness_spin.setRange(-30.0, -5.0)
        self.target_loudness_spin.setValue(-14.0)
        form.addRow("target_loudness_lufs", self.target_loudness_spin)
        layout.addWidget(top_fields)

        # YAML preview (collapsible)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setVisible(False)
        layout.addWidget(self.preview)
        self.preview_btn = QPushButton("Show YAML preview")
        self.preview_btn.setCheckable(True)
        self.preview_btn.toggled.connect(self._on_toggle_preview)
        layout.addWidget(self.preview_btn)

    # ----- form building -----
    def _build_param_form_for(self, tid: str) -> QWidget:
        """Auto-generate a form from the transform's pydantic schema."""
        spec = get(tid)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        widgets: dict[str, QWidget] = {}
        for name, field in spec.schema.model_fields.items():
            ann = field.annotation
            default = field.default if field.default is not None else spec.defaults.get(name)
            # Type → widget mapping:
            if ann is float or _is_optional_of(ann, float):
                w = QDoubleSpinBox()
                w.setDecimals(4)
                w.setRange(field.metadata[0].ge if field.metadata else -1e9,
                           field.metadata[-1].le if field.metadata else 1e9)
                w.setSingleStep(0.01)
                if default is not None:
                    w.setValue(float(default))
            elif ann is int:
                w = QSpinBox()
                w.setRange(int(field.metadata[0].ge) if field.metadata else -2**31,
                           int(field.metadata[-1].le) if field.metadata else 2**31 - 1)
                if default is not None:
                    w.setValue(int(default))
            elif ann is bool:
                w = QCheckBox()
                w.setChecked(bool(default))
            elif _is_literal(ann):
                w = QComboBox()
                for v in _literal_values(ann):
                    w.addItem(str(v), v)
            else:
                # Fallback: text edit (e.g. for Path)
                from PyQt6.QtWidgets import QLineEdit
                w = QLineEdit(str(default) if default is not None else "")
            form.addRow(name, w)
            widgets[name] = w
        self._field_widgets[tid] = widgets
        return form_widget

    # ----- event handlers -----
    def _on_profile_select(self, idx: int) -> None: ...
    def _on_transform_select(self, item: QTreeWidgetItem, col: int) -> None: ...
    def _on_transform_check_changed(self, item: QTreeWidgetItem, col: int) -> None: ...
    def _on_save(self) -> None:
        prof = self._collect_profile()
        try:
            Profile.model_validate(prof.model_dump())  # re-validate
        except ValidationError as exc:
            QMessageBox.critical(self, "Validation error", str(exc))
            return
        # Backup existing file
        if self.current_path and self.current_path.exists():
            self.current_path.rename(self.current_path.with_suffix(".yaml.bak"))
        dump_profile(prof, self.current_path)
        QMessageBox.information(self, "Saved", f"Wrote {self.current_path}")

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save profile as", str(PROFILES_DIR / "my_profile.yaml"),
            "YAML (*.yaml)",
        )
        if path:
            prof = self._collect_profile()
            dump_profile(prof, Path(path))
            self._reload_profile_combo()

    def _on_new(self) -> None: ...
    def _on_toggle_preview(self, on: bool) -> None:
        self.preview.setVisible(on)
        if on:
            prof = self._collect_profile()
            self.preview.setPlainText(yaml.safe_dump(prof.model_dump(), sort_keys=False))

    def _collect_profile(self) -> Profile:
        """Read current form state → assemble Profile, pydantic-validate."""
        ...

    def _reload_profile_combo(self) -> None:
        ...
```

**Tests:** `tests/unit/test_gui_profile_editor.py`
- `test_load_cid_aware_roundtrip` — load cid_aware → save (no edits) → reload → values identical
- `test_edit_pitch_and_save` — load cid_aware → mutate `audio.pitch_tempo.pitch` 1.06→1.07 → save → reload → 1.07
- `test_disable_transform_persists` — uncheck `video.noise` → save → reload → enabled=false
- `test_save_creates_backup` — save over existing → `.yaml.bak` file created
- `test_invalid_value_rejected` — try set `pitch=3.0` → QMessageBox + no write
- `test_form_handles_all_18_transforms` — iterate `all_ids()` → param form builds without error for each

## Workitem 4 — History screen

**File:** `src/yt_uniquifier/gui/screens/history.py` (new)

```python
"""Past runs table with re-open / re-run actions."""

from __future__ import annotations
import webbrowser
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState, HistoryEntry


class HistoryScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._build_ui()
        self.state.history_changed.connect(self._refresh)
        self._refresh(self.state._history)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Filter
        row = QHBoxLayout()
        row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filename / profile name / status")
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit)
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.clicked.connect(self._clear_history)
        row.addWidget(self.clear_btn)
        layout.addLayout(row)

        # Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Date", "Source", "Profile", "Encoder", "Status", "Actions"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)

    def _refresh(self, history: list[HistoryEntry]) -> None:
        self.table.setRowCount(0)
        for entry in history:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(entry.timestamp))
            self.table.setItem(r, 1, QTableWidgetItem(Path(entry.source_path).name))
            self.table.setItem(r, 2, QTableWidgetItem(entry.profile_name))
            self.table.setItem(r, 3, QTableWidgetItem(entry.encoder_name))
            self.table.setItem(r, 4, QTableWidgetItem(entry.status))
            # Actions cell: 3 buttons (Open output / Open QA / Re-run)
            actions = self._build_actions_cell(entry)
            self.table.setCellWidget(r, 5, actions)

    def _show_context_menu(self, pos) -> None: ...
    def _build_actions_cell(self, entry: HistoryEntry) -> QWidget: ...
    def _apply_filter(self, text: str) -> None: ...
    def _clear_history(self) -> None: ...
```

**Tests:** `tests/unit/test_gui_history_screen.py`
- `test_history_refreshes_on_signal` — `state.push_history(entry)` → table grows by 1 row
- `test_history_filter` — filter text → only matching rows visible
- `test_history_clear` — clear button → state has empty history

## Workitem 5 — History hooks in workers

**File:** `src/yt_uniquifier/gui/workers/run_worker.py` — modify
`RunWorker` to push HistoryEntry in `finished_ok` flow.

Update `_build_qa()` return + emit. After QA built, get the `AppState`
ref (passed in constructor) and push:

```python
class RunWorker(WorkerBase):
    def __init__(self, plan, options, *, state: AppState | None = None, ...):
        ...
        self.state = state

    def run(self):
        ...
        if self.state is not None:
            from datetime import datetime
            self.state.push_history(HistoryEntry(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                source_path=str(self.plan.source.path),
                profile_name=self.plan.profile.name,
                encoder_name=self.plan.encoder.name,
                output_path=str(self.options.output),
                qa_html_path=str(qa_html) if qa_html else None,
                plan_hash=self.plan.plan_hash,
                status="done",
            ))
```

Similar hooks in `BatchWorker.file_done` (per-file entry) and
`CalibrateWorker.finished_ok` (single entry per calibrate session).

**Tests:** `tests/unit/test_gui_history_integration.py`
- `test_runworker_pushes_history_on_done` — mock done → state.history has 1 entry
- `test_runworker_pushes_failed_history_on_exception` — failure → entry status=failed
- `test_batch_worker_pushes_history_per_file` — 3 files succeed → 3 entries

## Workitem 6 — Wire Profile Editor to Run screen

**File:** `src/yt_uniquifier/gui/screens/run.py` (modify)

Enable the disabled "Edit…" button, wire it to the Profile Editor
screen. Use a shared signal or simply ask MainWindow to switch tabs:

```python
self.edit_profile_btn.setEnabled(True)
self.edit_profile_btn.setToolTip("")
self.edit_profile_btn.clicked.connect(self._on_edit_profile)

def _on_edit_profile(self):
    # Emit a signal that MainWindow catches to switch sidebar + pre-load profile.
    self.edit_profile_requested.emit(self.profile_combo.currentData())
```

In `MainWindow`: connect `run_screen.edit_profile_requested` to a slot
that sets sidebar row to ProfileEditor index and loads the path.

## Workitem 7 — Sidebar registration

**File:** `src/yt_uniquifier/gui/app_pyqt.py` — update:

```python
from yt_uniquifier.gui.screens.qa_viewer import QaViewerScreen
from yt_uniquifier.gui.screens.profile_editor import ProfileEditorScreen
from yt_uniquifier.gui.screens.history import HistoryScreen

SIDEBAR_ITEMS = [
    ("Run",            RunScreen),
    ("Batch",          BatchScreen),
    ("Calibrate",      CalibrateScreen),
    ("QA Viewer",      QaViewerScreen),               # was placeholder
    ("Profile Editor", ProfileEditorScreen),          # was placeholder
    ("History",        HistoryScreen),                # was placeholder
    # 4 remaining placeholders: Corpus, Queue, Validation, Settings
]
```

## Acceptance

```bash
# 1. Launch.
yt-uniq-gui

# 2. Run screen → "Edit…" → switches to Profile Editor with selected profile loaded.

# 3. Profile Editor → change pitch from 1.06 to 1.07 → Save as my.yaml.
# 4. Switch to Run screen → profile combo now includes my.yaml.

# 5. Run a short clip → done → History gets a row.

# 6. Switch to History → row visible → click "Open QA report" → opens in QA Viewer.

# 7. Switch to QA Viewer → "Open existing" tab → pick a .qa.html → renders.

# 8. QA Viewer → "Compute new" tab → pick input + output → Compute → embedded report renders.

# 9. PyQt6-WebEngine removed:
pip uninstall PyQt6-WebEngine -y
yt-uniq-gui   # QA Viewer shows fallback label + "Open in browser" works.

# 10. Tests.
pytest -q tests/unit/test_gui_qa_worker* tests/unit/test_gui_qa_viewer* \
         tests/unit/test_gui_profile_editor* tests/unit/test_gui_history*
ruff check . && mypy src/yt_uniquifier/gui
```

## Tests

| Уровень | Файл | Кол-во |
|---|---|---|
| Unit | `test_gui_qa_worker.py` | 3 |
| Unit | `test_gui_qa_viewer.py` | 3 |
| Unit | `test_gui_profile_editor.py` | 6 |
| Unit | `test_gui_history_screen.py` | 3 |
| Unit | `test_gui_history_integration.py` | 3 |
| **Total** | | **~18 new** |

## Risks

| Риск | Митигация |
|---|---|
| `QWebEngineView` heavy dep (~150 MB), users без [gui] не могут import | Graceful fallback на label + "Open in browser"; HAS_WEBENGINE check |
| QWebEngineView не рендерит styled qa.html | Test early on real fixture; если стили ломаются → enable JS via `WebEngineProfile` settings, или fallback to browser |
| Profile Editor's auto-form generation ломается на новых transforms | Iterating `all_ids()` в `test_form_handles_all_18_transforms` ловит регрессии при добавлении transforms |
| Saving over a shipped profile (cid_aware.yaml) разрушает дефолт | Always create `.yaml.bak` backup; consider warning dialog "you're editing a shipped profile" |
| History grows unbounded | Cap at HISTORY_CAP=100, oldest evicted (already в `AppState.push_history`) |
| Pydantic validation error message — multi-line, ugly | QMessageBox.critical with detailed_text для нормального отображения многострочной ошибки |
| Profile combo не обновляется в Run/Batch/Calibrate после save | AppState emits `profiles_changed` signal; affected combos refresh |

## Hand-off

После v0.5.2:

- 6 functional screens (Run, Batch, Calibrate, QA Viewer, Profile
  Editor, History).
- 4 placeholders: Corpus, Queue, Validation, Settings.
- History persistence работает; все workers пишут entries.
- Profile editor поддерживает все 18 transforms через auto-form generation.
- ~18 новых тестов.

Tag: `v0.5.2`.

## Effort

| Item | Time |
|---|---|
| 1. `QaWorker` + 3 tests | 1 hour |
| 2. QA Viewer screen + fallback + 3 tests | 2 hours |
| 3. Profile Editor screen (auto-form gen for 18 transforms) | 4 hours |
| 4. Profile Editor tests (6) | 1.5 hours |
| 5. History screen + 3 tests | 2 hours |
| 6. History hooks in workers + 3 tests | 1 hour |
| 7. Profile Editor ↔ Run screen wiring | 30 min |
| 8. Sidebar wiring + commit + tag | 30 min |
| 9. Cross-screen smoke (manual) | 30 min |
| **Total** | **~13 hours / ~3 working days** |
