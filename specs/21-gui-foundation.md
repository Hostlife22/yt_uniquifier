# Spec 21 — GUI foundation: app shell, sidebar nav, Run screen (v0.5.0)

> **Phase 21 (v0.5.0)** · 3 days · **Deps:** v0.4.x core stable

## Context

Replace the 260-line `gui/app_pyqt.py` single-window shell with a
multi-screen app skeleton + the Run screen as the first concrete screen.
Establishes the patterns (sidebar nav, `WorkerBase`, `AppState`,
reusable widgets, theme system) that all subsequent specs build on.

After this spec ships, the GUI does NOT yet cover Batch/Calibrate/QA
Viewer/etc. — those land in Specs 22–25. But the Run screen alone is
already a significant UX upgrade over v0.4: probe metadata on file drop,
preflight findings inline before run, segment timeline visualization,
KPI pills after.

## Goal

After v0.5.0:

- `yt-uniq-gui` opens a window with a sidebar listing 10 screens
  (Run, Batch, Calibrate, QA Viewer, Corpus, Queue, Validation, Profile
  Editor, History, Settings). Only **Run** is implemented; the rest
  show "Coming in v0.5.x" placeholder.
- Run screen end-to-end: drag-drop file → metadata + preflight →
  pick profile/encoder → Run → segment timeline + live log → done with
  KPI pills + "Open QA report" button.
- 6 reusable widgets shipped: `FilePickerRow`, `EncoderSelector`,
  `PreflightPanel`, `SegmentTimeline`, `LogConsole`, `KpiPills`.
- 2 workers shipped: `RunWorker` (refactored from current `Worker`),
  `ProbeWorker` (new, async file metadata).
- `AppState` with `~/.config/yt_uniquifier/state.json` persistence.
- `theme.py` with dark + light QSS tokens, dark default.
- 12+ unit tests via `pytest-qt`.
- Tag: `v0.5.0`.

## Scope

**In:**
- App shell with `QListWidget` sidebar + `QStackedWidget` content area
- `ScreenBase` lifecycle abstraction
- `AppState` + persistence
- `WorkerBase` base class
- 6 reusable widgets
- Run screen
- `RunWorker` refactor + `ProbeWorker` new
- `theme.py` (dark token set + light token set; QSS templates)
- Tests for widgets + workers

**Not in (deferred to 22–25):**
- Other 9 screens (placeholders only)
- Profile editor (it's referenced from Run screen but lands in 23)
- History persistence (entries created but pane not visible until 23)
- Settings screen UI (config defaults are read; UI is 25)
- Theme switcher UI (theme picked from `AppState`; switcher widget is 25)

## Workitem 1 — `WorkerBase` and refactor existing `RunWorker`

**File:** `src/yt_uniquifier/gui/workers/base.py` (new)

```python
"""Common base class for all GUI workers."""

from __future__ import annotations

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyQt6 is not installed. Install: pip install 'yt-uniquifier[gui]'"
    ) from exc

from yt_uniquifier.core.runner import CancelToken


class WorkerBase(QThread):
    """Common GUI worker contract: cancellable, emits typed signals.

    Subclasses override `run()` and add their own signals as needed
    (e.g. CalibrateWorker.step(dict), BatchWorker.file_done(str)).
    """

    started_ = pyqtSignal()                  # underscore avoids QThread.started clash
    finished_ok = pyqtSignal(object)         # result payload (dict/path/etc.)
    failed = pyqtSignal(str)                 # error message
    log = pyqtSignal(str)                    # log line
    progress = pyqtSignal(float, str)        # fraction [0..1], status message

    def __init__(self) -> None:
        super().__init__()
        self.cancel_token = CancelToken()

    def request_cancel(self) -> None:
        self.cancel_token.cancel()
```

**File:** `src/yt_uniquifier/gui/workers/run_worker.py` (refactored from
existing `gui/worker.py`)

Move the body of `gui/worker.py::Worker` here, change base to
`WorkerBase`. Keep the existing public signals (`progress`, `log`,
`finished_ok`, `failed`) for back-compat — existing `gui/app_pyqt.py`
that we're about to rewrite still imports `Worker` until we delete it.

```python
"""QThread wrapper around `orchestrator.run_full`."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.orchestrator import RunOptions, RunSummary, run_full
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.core.runner import RunEvent
from yt_uniquifier.gui.workers.base import WorkerBase


class RunWorker(WorkerBase):
    """Forward RunEvents from orchestrator.run_full as Qt signals."""

    finished_ok = pyqtSignal(str, str)         # output_path, qa_html_path (override base)

    def __init__(
        self, plan: Plan, options: RunOptions,
        *, run_qa: bool = True, fast_qa: bool = False,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.options = options
        self.run_qa = run_qa
        self.fast_qa = fast_qa
        self._total_us = max(int(plan.source.duration_sec * 1_000_000), 1)
        self._seg_us: dict[int, int] = {}

    # ... (body identical to current gui/worker.py::Worker.run + _on_event + _build_qa)
```

**File:** `src/yt_uniquifier/gui/worker.py` (kept as thin re-export shim)

```python
"""Backwards-compat shim. Real implementation in workers/run_worker.py."""

from yt_uniquifier.gui.workers.run_worker import RunWorker as Worker

__all__ = ["Worker"]
```

**Tests:** `tests/unit/test_gui_worker_base.py`
- `test_workerbase_signals_defined` — все 5 signals существуют
- `test_workerbase_cancel_token_set` — `request_cancel()` sets `cancel_token.is_cancelled()`
- `test_runworker_finishes_on_done_event` — mock run_full → emits finished_ok
- `test_runworker_progress_accumulates_per_segment` — multiple progress events update fraction
- `test_runworker_failed_on_exception` — run_full raises → failed signal

## Workitem 2 — `ProbeWorker` (new)

**File:** `src/yt_uniquifier/gui/workers/probe_worker.py` (new)

```python
"""Quick ffprobe wrapper for showing source metadata on file drop."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.probe import probe
from yt_uniquifier.gui.workers.base import WorkerBase


class ProbeWorker(WorkerBase):
    probed = pyqtSignal(object)                # SourceMeta on success

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        try:
            meta = probe(self.path)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.probed.emit(meta)
        self.finished_ok.emit(meta)
```

**Tests:** `tests/unit/test_gui_probe_worker.py`
- `test_probe_worker_emits_meta` — mock probe() → probed signal carries SourceMeta
- `test_probe_worker_failed_on_invalid_path` — non-existent file → failed signal

## Workitem 3 — `AppState` + persistence

**File:** `src/yt_uniquifier/gui/state.py` (new)

```python
"""Single source of truth for GUI selections, recents, history.

Persisted to ~/.config/yt_uniquifier/state.json and history.json.
"""

from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

CONFIG_DIR = Path.home() / ".config" / "yt_uniquifier"
STATE_PATH = CONFIG_DIR / "state.json"
HISTORY_PATH = CONFIG_DIR / "history.json"

RECENTS_CAP = 20
HISTORY_CAP = 100


@dataclass
class HistoryEntry:
    timestamp: str
    source_path: str
    profile_name: str
    encoder_name: str
    output_path: str
    qa_html_path: str | None
    plan_hash: str
    status: str  # "done", "failed", "cancelled"


class AppState(QObject):
    """Mutable global state. Screens subscribe to changes via Qt signals."""

    input_path_changed = pyqtSignal(object)              # Path | None
    output_path_changed = pyqtSignal(object)
    profile_path_changed = pyqtSignal(object)
    encoder_name_changed = pyqtSignal(object)            # str | None ('auto' = None)
    theme_changed = pyqtSignal(str)                      # 'dark'|'light'|'system'
    recents_changed = pyqtSignal(list)                   # list[str]
    history_changed = pyqtSignal(list)                   # list[HistoryEntry]

    def __init__(self) -> None:
        super().__init__()
        self._input_path: Path | None = None
        self._output_path: Path | None = None
        self._profile_path: Path | None = None
        self._encoder_name: str | None = None
        self._theme: str = "dark"
        self._recents: list[str] = []
        self._history: list[HistoryEntry] = []
        self._load()

    # ----- setters with signal emission -----
    def set_input_path(self, path: Path | None) -> None: ...
    def set_output_path(self, path: Path | None) -> None: ...
    def set_profile_path(self, path: Path | None) -> None: ...
    def set_encoder_name(self, name: str | None) -> None: ...
    def set_theme(self, theme: str) -> None: ...

    # ----- recents -----
    def push_recent(self, path: str) -> None:
        """Add to front, dedup, cap at RECENTS_CAP."""
        ...

    # ----- history -----
    def push_history(self, entry: HistoryEntry) -> None:
        """Prepend entry, cap at HISTORY_CAP, persist."""
        ...

    # ----- persistence -----
    def _load(self) -> None: ...
    def save(self) -> None: ...
```

**Tests:** `tests/unit/test_gui_app_state.py`
- `test_state_roundtrip` — set → save → load fresh → values match
- `test_recents_dedup_and_cap` — pushing same path twice keeps it once; >20 evicts oldest
- `test_history_persistence` — push HistoryEntry → save → reload → entry present
- `test_history_cap` — 101 entries → oldest evicted
- `test_signals_emitted` — set_input_path → input_path_changed fires

## Workitem 4 — `theme.py` (QSS tokens + builder)

**File:** `src/yt_uniquifier/gui/theme.py` (new)

```python
"""QSS theme tokens + builder. Dark default; light + system future-ready."""

from __future__ import annotations
from typing import Literal

ThemeName = Literal["dark", "light", "system"]


DARK_TOKENS = {
    "bg":             "#1f1f2b",
    "bg_alt":         "#2a2a37",
    "bg_deep":        "#16161e",
    "fg":             "#e2e2e8",
    "fg_dim":         "#9b9ba8",
    "accent":         "#3b6ea8",
    "accent_hover":   "#4f87c4",
    "accent_warm":    "#d18b3b",
    "danger":         "#a83b3b",
    "danger_hover":   "#c44f4f",
    "success":        "#3ba85c",
    "warning":        "#d1a93b",
    "border":         "#444",
}

LIGHT_TOKENS = {
    "bg":             "#f5f5f8",
    "bg_alt":         "#ffffff",
    "bg_deep":        "#eaeaef",
    "fg":             "#1f1f2b",
    "fg_dim":         "#6a6a78",
    "accent":         "#2b5e98",
    "accent_hover":   "#3877b8",
    "accent_warm":    "#c17a2b",
    "danger":         "#a83b3b",
    "danger_hover":   "#c44f4f",
    "success":        "#2c8c4a",
    "warning":        "#b8902f",
    "border":         "#ccc",
}


def qss_for(theme: ThemeName) -> str:
    """Return the full QSS stylesheet for the given theme."""
    tokens = DARK_TOKENS if theme in ("dark", "system") else LIGHT_TOKENS  # MVP: system→dark
    return _QSS_TEMPLATE.format(**tokens)


_QSS_TEMPLATE = """
QWidget {{ background: {bg}; color: {fg}; font-size: 13px; }}
QPushButton {{
    background: {accent}; color: white; padding: 6px 14px;
    border-radius: 4px; border: none;
}}
QPushButton:disabled {{ background: {fg_dim}; color: {bg}; }}
QPushButton:hover:!disabled {{ background: {accent_hover}; }}
QPushButton#cancel {{ background: {danger}; }}
QPushButton#cancel:hover {{ background: {danger_hover}; }}
QPushButton#run {{ background: {accent_warm}; font-weight: bold; padding: 8px 18px; }}
QListWidget#sidebar {{
    background: {bg_deep}; border: none; padding: 8px 0;
}}
QListWidget#sidebar::item {{
    padding: 10px 16px; color: {fg_dim};
}}
QListWidget#sidebar::item:selected {{
    background: {accent}; color: white;
}}
QLabel#path {{ background: {bg_alt}; padding: 5px 8px; border-radius: 3px; }}
QLabel#status {{ color: {fg_dim}; }}
QComboBox {{ background: {bg_alt}; padding: 4px 8px; border-radius: 3px;
            border: 1px solid {border}; }}
QProgressBar {{
    background: {bg_alt}; border-radius: 3px; text-align: center;
    color: white; padding: 1px;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
QTextEdit, QPlainTextEdit {{
    background: {bg_deep}; color: {fg};
    font-family: SFMono-Regular, Menlo, monospace; font-size: 11px;
}}
QTableWidget {{ background: {bg_alt}; gridline-color: {border}; }}
QHeaderView::section {{ background: {bg_deep}; color: {fg_dim}; padding: 4px; border: none; }}
.pill {{ padding: 4px 10px; border-radius: 10px; font-weight: 600; }}
.pill.green {{ background: {success}; color: white; }}
.pill.yellow {{ background: {warning}; color: {bg_deep}; }}
.pill.red {{ background: {danger}; color: white; }}
"""
```

**Tests:** `tests/unit/test_gui_theme.py`
- `test_qss_dark_contains_tokens` — `qss_for("dark")` includes expected color values
- `test_qss_light_contains_tokens` — `qss_for("light")` includes expected color values

## Workitem 5 — Six reusable widgets

**Files:** `src/yt_uniquifier/gui/widgets/*.py` (6 new modules)

| Module | Class | Public API |
|---|---|---|
| `file_picker.py` | `FilePickerRow(QWidget)` | `__init__(label: str, kind: 'input'|'output', filter: str = '...', state: AppState)`. Signal: `path_changed(Path)`. Drag-drop target, "Browse…" button, recents dropdown from `state.recents`. |
| `encoder_selector.py` | `EncoderSelector(QWidget)` | `__init__(state: AppState)`. Signal: `encoder_changed(object)` — str or None. Populated from `detect_encoders()`; `works=False` entries disabled with tooltip. |
| `preflight_panel.py` | `PreflightPanel(QWidget)` | `set_findings(list[PreflightFinding])`. Signal: `has_fail(bool)`. Coloured rows with code + message + suggestion (collapsible). |
| `segment_timeline.py` | `SegmentTimeline(QWidget)` | `init(n_segments: int)`, `update_segment(idx: int, status: str)`. Horizontal bar, N coloured cells. |
| `log_console.py` | `LogConsole(QTextEdit)` | `log(line: str, level: str = 'info')`. Filter combo (all/progress/log/error), copy-to-clipboard button. Cap at `max_lines=2000`. |
| `kpi_pills.py` | `KpiPills(QWidget)` | `set_qa(qa_json: dict)`. Horizontal row of `.pill` chips colored by KPI band. |

**Implementation note:** all widgets soft-import `PyQt6` so the module
can be imported without `[gui]` extra installed.

**Tests:** `tests/unit/test_gui_widgets.py` (one section per widget)
- `test_file_picker_emits_on_browse` — click "Browse" → mock dialog → `path_changed` fires
- `test_encoder_selector_lists_detected` — mock `detect_encoders()` → combo populated
- `test_encoder_selector_disabled_unavailable` — `works=False` entries are disabled
- `test_preflight_panel_colour_by_severity` — fail/warn/ok rows have expected style class
- `test_preflight_panel_emits_has_fail` — finding with severity=fail → `has_fail(True)`
- `test_segment_timeline_updates_cell` — `update_segment(2, "done")` colors cell 2 green
- `test_log_console_caps_lines` — 2500 lines logged → only 2000 retained
- `test_kpi_pills_colors_by_target` — pHash 0.92 → red pill, 0.78 → yellow, 0.65 → green

Total: ~16 widget tests via pytest-qt.

## Workitem 6 — App shell rewrite

**File:** `src/yt_uniquifier/gui/app_pyqt.py` (rewrite)

```python
"""MainWindow with sidebar navigation + QStackedWidget content area."""

from __future__ import annotations
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QStackedWidget, QStatusBar, QWidget,
)

from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.screens.run import RunScreen
# Placeholder screens for v0.5.0 — implemented in 22-25.
from yt_uniquifier.gui.screens.base import PlaceholderScreen
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.theme import qss_for


SIDEBAR_ITEMS = [
    ("Run",            RunScreen),
    ("Batch",          lambda s: PlaceholderScreen("Batch", "v0.5.1")),
    ("Calibrate",      lambda s: PlaceholderScreen("Calibrate", "v0.5.1")),
    ("QA Viewer",      lambda s: PlaceholderScreen("QA Viewer", "v0.5.2")),
    ("Profile Editor", lambda s: PlaceholderScreen("Profile Editor", "v0.5.2")),
    ("History",        lambda s: PlaceholderScreen("History", "v0.5.2")),
    ("Corpus",         lambda s: PlaceholderScreen("Corpus", "v0.5.4")),
    ("Queue",          lambda s: PlaceholderScreen("Queue / Worker", "v0.5.3")),
    ("Validation",     lambda s: PlaceholderScreen("Validation", "v0.5.3")),
    ("Settings",       lambda s: PlaceholderScreen("Settings", "v0.5.4")),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("yt-uniquifier")
        self.resize(1100, 720)

        self.state = AppState()
        self.setStyleSheet(qss_for(self.state._theme))

        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        for label, _factory in SIDEBAR_ITEMS:
            QListWidgetItem(label, self.sidebar)
        layout.addWidget(self.sidebar)

        # Stack
        self.stack = QStackedWidget()
        for _label, factory in SIDEBAR_ITEMS:
            self.stack.addWidget(factory(self.state))
        layout.addWidget(self.stack, stretch=1)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
```

**File:** `src/yt_uniquifier/gui/screens/base.py` (new)

```python
"""ScreenBase + PlaceholderScreen — common contract for all screens."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from yt_uniquifier.gui.state import AppState


class ScreenBase(QWidget):
    """All screens take AppState and a parent (none in MVP)."""

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state


class PlaceholderScreen(ScreenBase):
    """Stub shown for screens not yet implemented in this release."""

    def __init__(self, name: str, lands_in: str) -> None:
        # Bypass ScreenBase signature for placeholders.
        super(ScreenBase, self).__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(QLabel(f"<h2>{name}</h2>"))
        layout.addWidget(QLabel(f"Coming in {lands_in}."))
```

## Workitem 7 — Run screen

**File:** `src/yt_uniquifier/gui/screens/run.py` (new)

```python
"""Single-file Run screen. Replaces the entire v0.4 GUI flow."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.orchestrator import RunOptions, build_plan
from yt_uniquifier.core.preflight import preflight
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
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._build_ui()
        self.run_worker: RunWorker | None = None
        self.probe_worker: ProbeWorker | None = None
        self.qa_html_path: Path | None = None

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # File pickers
        self.input_picker = FilePickerRow("Input video:", "input",
                                          "*.mp4 *.mov *.mkv *.webm", self.state)
        self.input_picker.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.input_picker)

        self.output_picker = FilePickerRow("Output:", "output", "*.mp4", self.state)
        self.output_picker.path_changed.connect(self.state.set_output_path)
        layout.addWidget(self.output_picker)

        # Profile + encoder
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        opts.addWidget(self.profile_combo, stretch=1)

        self.edit_profile_btn = QPushButton("Edit…")
        self.edit_profile_btn.setEnabled(False)  # enabled when Profile Editor lands in 23
        self.edit_profile_btn.setToolTip("Coming in v0.5.2 (Profile Editor)")
        opts.addWidget(self.edit_profile_btn)

        opts.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        opts.addWidget(self.encoder_selector, stretch=1)
        layout.addLayout(opts)

        # Preflight
        self.preflight_panel = PreflightPanel()
        self.preflight_panel.has_fail.connect(self._on_has_fail)
        layout.addWidget(self.preflight_panel)

        # Action buttons
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

        # Segment timeline
        self.timeline = SegmentTimeline()
        layout.addWidget(self.timeline)

        # KPI pills (shown only after run finishes)
        self.kpi_pills = KpiPills()
        layout.addWidget(self.kpi_pills)

        # Log
        self.log = LogConsole()
        layout.addWidget(self.log, stretch=1)

        self._refresh_run_button_state()

    # ----- event handlers -----
    def _on_input_changed(self, path: Path) -> None:
        self.state.set_input_path(path)
        self._refresh_run_button_state()
        # Auto-probe
        self.probe_worker = ProbeWorker(path)
        self.probe_worker.probed.connect(self._on_probed)
        self.probe_worker.failed.connect(lambda msg: self.log.log(f"probe: {msg}", "error"))
        self.probe_worker.start()

    def _on_probed(self, meta) -> None:
        self.log.log(
            f"probed: {meta.video[0].codec} {meta.video[0].width}×{meta.video[0].height} "
            f"@ {meta.video[0].fps:.2f}fps, {meta.duration_sec:.1f}s", "info",
        )

    def _on_preflight(self) -> None:
        # ... build plan, call preflight(), set findings on panel
        ...

    def _on_run(self) -> None:
        # ... same flow as current GUI but use RunWorker + emit timeline updates
        ...

    def _on_cancel(self) -> None:
        if self.run_worker is not None:
            self.run_worker.request_cancel()

    def _on_open_qa(self) -> None:
        import webbrowser
        if self.qa_html_path is not None:
            webbrowser.open(self.qa_html_path.as_uri())

    def _on_has_fail(self, has_fail: bool) -> None:
        self.run_btn.setEnabled(not has_fail and self._inputs_ready())

    def _inputs_ready(self) -> bool:
        return self.state._input_path is not None and self.state._output_path is not None

    def _refresh_run_button_state(self) -> None:
        self.run_btn.setEnabled(self._inputs_ready() and self.run_worker is None)
```

**Tests:** `tests/unit/test_gui_run_screen.py`
- `test_run_screen_initial_state` — Run button disabled until both paths set
- `test_run_screen_preflight_blocks_run_on_fail` — fail finding → Run disabled
- `test_run_screen_kpi_pills_populated_on_done` — finished_ok → KPI pills show

## Acceptance

```bash
# 1. Install with new deps.
pip install -e ".[dev,gui]"

# 2. Launch.
yt-uniq-gui
# Expected: window with sidebar (10 entries), Run screen as default.

# 3. Drop a 30s mp4 onto Input picker.
# Expected: log shows probed metadata within ~1s.

# 4. Click "Run preflight".
# Expected: PreflightPanel populates with findings (likely warnings on short clip).

# 5. Click Run.
# Expected: SegmentTimeline animates, log streams ffmpeg events, KPI pills appear on done.

# 6. Click "Open QA report".
# Expected: browser opens the qa.html.

# 7. Click sidebar entries 2-10.
# Expected: each shows "Coming in v0.5.x" placeholder.

# 8. Headless smoke (CI).
QT_QPA_PLATFORM=offscreen python -c "
from yt_uniquifier.gui.app_pyqt import MainWindow, QApplication
import sys
app = QApplication(sys.argv); w = MainWindow(); w.show()
app.processEvents(); print('OK'); app.quit()
"

# 9. Tests.
pytest -q tests/unit/test_gui_*
# Expected: 16+ widget tests + 5 worker tests + 5 state tests + 3 run-screen tests all pass.

# 10. Lint + types.
ruff check . && mypy src/yt_uniquifier/gui
```

## Tests

| Уровень | Файл | Кол-во |
|---|---|---|
| Unit | `test_gui_worker_base.py` | 5 |
| Unit | `test_gui_probe_worker.py` | 2 |
| Unit | `test_gui_app_state.py` | 5 |
| Unit | `test_gui_theme.py` | 2 |
| Unit | `test_gui_widgets.py` (6 sections) | 16 |
| Unit | `test_gui_run_screen.py` | 3 |
| Smoke | `test_gui_full_launch.py` (headless) | 1 |
| **Total** | | **~34 new** |

## Risks

| Риск | Митигация |
|---|---|
| `pytest-qt` не работает на CI без offscreen platform | `tests/conftest.py` устанавливает `QT_QPA_PLATFORM=offscreen` для `tests/unit/test_gui_*` |
| Sidebar навигация не подсвечивает текущий screen на light theme | QSS `::item:selected` background — задан явно для обеих тем |
| `FilePickerRow` drag-drop не работает на Linux Wayland | Стандартный `QDragEnterEvent` handler; если Wayland специфика — fallback на "Browse" button (already present) |
| `EncoderSelector` блокирует UI на ~3s при первом запуске (`detect_encoders` real test-run) | Encoder detection async через subprocess; UI показывает "Detecting encoders…" placeholder; результат подменяется в combo по готовности |
| `AppState` race condition между screens | Все мутации через `set_*` методы на main thread; signals doc'ed as main-thread-only |
| Refactor of `gui/worker.py` ломает existing test (если есть) | Shim re-export `Worker = RunWorker` сохраняет import path |

## Hand-off

После v0.5.0:

- Sidebar navigation работает; 10 screens registered, 1 functional.
- Run screen покрывает текущий flow + adds: auto-probe on drop,
  preflight inline, segment timeline, KPI pills.
- `WorkerBase`, `AppState`, `theme.py`, 6 reusable widgets готовы для
  переиспользования в Specs 22–25.
- ~34 новых тестов проходят.
- Backwards compat: `gui.worker.Worker` shim сохраняет import path.

Tag: `v0.5.0`.

## Effort

| Item | Time |
|---|---|
| 1. `WorkerBase` + refactor `RunWorker` + shim | 1 hour |
| 2. `ProbeWorker` | 30 min |
| 3. `AppState` + persistence + tests | 2 hours |
| 4. `theme.py` + tests | 1 hour |
| 5. 6 widgets + ~16 tests | 6 hours |
| 6. App shell + ScreenBase + PlaceholderScreen | 1.5 hours |
| 7. Run screen | 3 hours |
| 8. Smoke test, lint, type-check, commit, tag | 1 hour |
| **Total** | **~16 hours / 3 working days** |
