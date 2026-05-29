# Spec 24 — Queue dashboard + Validation wizard (v0.5.3)

> **Phase 24 (v0.5.3)** · 3 days · **Deps:** v0.5.2 + v0.4.1 (Spec 18 — validation harness landed)

## Context

Two screens covering the distributed-batch and real-CID-validation
workflows from CLI:

- **Queue / Worker dashboard** — wraps `core.queue.leasing.FileQueue` +
  `cli/cmd_worker.py` flow. UI for `yt-uniq queue init/add/status/reset`
  + `yt-uniq worker` drain loop. Live status banner polls every 2s.
- **Validation wizard** — 3-step UI for the v0.4.1 manual real-CID
  validation harness (`tools/generate_variants.py` + `validation_log.csv`
  + `tools/validation_correlate.py`).

The Validation screen explicitly depends on v0.4.1 being shipped — those
tools must exist before this UI is meaningful. Queue dashboard depends
only on `core.queue.leasing` which already exists since v0.3.0.

## Goal

After v0.5.3:

- **Queue / Worker screen** opens via sidebar. Two sub-tabs:
  - **Queue management** — pick root dir, 4 bucket lists, "Add files",
    "Init queue", "Reset stale", auto-refreshing stats every 2s.
  - **Worker control** — "Start worker" spawns a `QueueWorker`, "Stop"
    sets cancel token, log streams lease/run/release events.
- **Validation screen** opens via sidebar. 3-step wizard:
  - **Generate** — input + profile + N → spawns N runs → manifest.json.
  - **Record** — editable table for upload_date / video_id /
    match_status; "Save" appends to `validation_log.csv`.
  - **Analyze** — runs correlation analysis, prints Spearman ρ per
    predictor + verdict.
- 2 sidebar placeholders removed (Queue, Validation).
- Tag: `v0.5.3`.

## Scope

**In:**
- `QueueStatusWorker` (polling) — emits stats dict every 2s.
- `QueueWorker` (drainer) — wraps `cli/cmd_worker.py` logic via
  `FileQueue.lease()` + `orchestrator.run_full` + `release_*`.
- Queue / Worker screen with two sub-tabs.
- `GenerateVariantsWorker` — wraps `tools/generate_variants.py` logic
  (or calls it as subprocess if cleaner).
- `CorrelationWorker` — wraps `tools/validation_correlate.py`.
- Validation screen with 3-step wizard.
- Tests for both workers + screen-level smoke for state transitions.

**Not in:**
- Multi-machine queue coordination from GUI (it's already supported by
  shared-FS; users start workers on each machine independently).
- Auto-detecting NFS / ZFS / ext4 / s3fs quirks in the GUI (CLI's
  `queue init` already verifies atomic-rename; same check is invoked
  from GUI).
- ML model trained on `validation_log.csv` data (v0.6+ when ≥30 samples
  exist).
- Automated YouTube upload (TOS risk — wizard stays manual record).

## Workitem 1 — `QueueStatusWorker`

**File:** `src/yt_uniquifier/gui/workers/queue_status_worker.py` (new)

```python
"""Polls FileQueue.stats() every poll_sec seconds and emits stats dict."""

from __future__ import annotations
from pathlib import Path
from time import sleep

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.queue.leasing import FileQueue, queue_layout
from yt_uniquifier.gui.workers.base import WorkerBase


class QueueStatusWorker(WorkerBase):
    """Long-running poller. Emits stats every poll_sec until cancelled."""

    stats = pyqtSignal(dict)              # {"pending": N, "in_progress": N, "done": N, "failed": N}

    def __init__(self, root: Path, *, poll_sec: float = 2.0) -> None:
        super().__init__()
        self.root = root
        self.poll_sec = poll_sec

    def run(self) -> None:
        try:
            layout = queue_layout(self.root)
            q = FileQueue(layout)
        except Exception as exc:
            self.failed.emit(f"queue not initialised: {exc}")
            return

        while not self.cancel_token.is_cancelled():
            try:
                s = q.stats()
                self.stats.emit(dict(s))
            except Exception as exc:
                self.log.emit(f"stats error: {exc}")
            # Sleep in small slices so cancel is responsive.
            slept = 0.0
            while slept < self.poll_sec and not self.cancel_token.is_cancelled():
                sleep(0.1)
                slept += 0.1
```

**Tests:** `tests/unit/test_gui_queue_status_worker.py`
- `test_stats_emitted_periodically` — mock FileQueue.stats → ≥2 emissions in 5s
- `test_cancelled_exits_loop` — cancel after first emission → loop exits within poll_sec
- `test_failed_when_queue_not_initialized` — non-existent dir → failed signal

## Workitem 2 — `QueueWorker` (drainer)

**File:** `src/yt_uniquifier/gui/workers/queue_worker.py` (new)

```python
"""Drain queue: lease → run_full → release_done/failed → repeat.

Mirror of cli/cmd_worker.py main loop but as a QThread for GUI control.
"""

from __future__ import annotations
from pathlib import Path
from time import sleep, time

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.queue.leasing import FileQueue, queue_layout
from yt_uniquifier.gui.workers.base import WorkerBase


class QueueWorker(WorkerBase):
    """One-process queue drainer. Heartbeats while encoding."""

    lease_acquired = pyqtSignal(str)             # source path
    file_done = pyqtSignal(str, str)             # source path, output path
    file_failed = pyqtSignal(str, str)           # source path, error

    def __init__(
        self,
        root: Path,
        profile: Profile,
        out_dir: Path,
        *,
        encoder_override: str | None = None,
        workers: int = 1,
        work_dir_root: Path | None = None,
        poll_sec: float = 3.0,
        heartbeat_sec: float = 30.0,
        stop_after_empty: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.profile = profile
        self.out_dir = out_dir
        self.encoder_override = encoder_override
        self.workers = workers
        self.work_dir_root = work_dir_root or Path.home() / ".cache" / "yt_uniquifier" / "worker"
        self.poll_sec = poll_sec
        self.heartbeat_sec = heartbeat_sec
        self.stop_after_empty = stop_after_empty

    def run(self) -> None:
        try:
            layout = queue_layout(self.root)
            q = FileQueue(layout)
        except Exception as exc:
            self.failed.emit(f"queue not initialised: {exc}")
            return

        self.out_dir.mkdir(parents=True, exist_ok=True)
        last_heartbeat = time()

        while not self.cancel_token.is_cancelled():
            lease = q.lease()
            if lease is None:
                if self.stop_after_empty:
                    self.log.emit("queue empty; exiting")
                    break
                sleep(self.poll_sec)
                continue

            src = lease.path
            self.lease_acquired.emit(str(src))
            out = self.out_dir / f"{src.stem}.uniq.mp4"
            try:
                plan = build_plan(src, self.profile, self.encoder_override)
                opts = RunOptions(
                    work_dir=self.work_dir_root / plan.plan_hash,
                    output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                    workers=self.workers,
                )
                run_full(
                    plan, opts,
                    on_event=lambda ev: self._heartbeat_if_due(q, last_heartbeat),
                    cancel_token=self.cancel_token,
                )
                q.release_done(lease)
                self.file_done.emit(str(src), str(out))
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                q.release_failed(lease, msg)
                self.file_failed.emit(str(src), msg)
            last_heartbeat = time()

        self.finished_ok.emit({"reason": "cancelled" if self.cancel_token.is_cancelled() else "queue_empty"})

    def _heartbeat_if_due(self, q: FileQueue, last: float) -> None:
        if time() - last > self.heartbeat_sec:
            q.heartbeat()
```

**Tests:** `tests/unit/test_gui_queue_worker.py`
- `test_empty_queue_stops_when_flag` — mock `lease() → None` + `stop_after_empty=True` → finished_ok
- `test_empty_queue_polls_when_flag_off` — mock `lease() → None` x2 + cancel → finished_ok cancelled
- `test_successful_lease_calls_release_done` — mock happy path → `release_done` called
- `test_failed_run_calls_release_failed` — mock raise → `release_failed` called

## Workitem 3 — Queue / Worker screen

**File:** `src/yt_uniquifier/gui/screens/queue.py` (new)

```python
"""Distributed queue dashboard + worker control."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.queue.leasing import FileQueue, init_queue, queue_layout
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.queue_status_worker import QueueStatusWorker
from yt_uniquifier.gui.workers.queue_worker import QueueWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class QueueScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.queue_root: Path | None = None
        self.status_worker: QueueStatusWorker | None = None
        self.drain_worker: QueueWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Queue root picker
        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("Queue root:"))
        self.root_label = QLabel("(none)")
        self.root_label.setObjectName("path")
        root_row.addWidget(self.root_label, stretch=1)
        self.pick_root_btn = QPushButton("Browse…")
        self.pick_root_btn.clicked.connect(self._pick_root)
        root_row.addWidget(self.pick_root_btn)
        self.init_btn = QPushButton("Init queue here")
        self.init_btn.clicked.connect(self._init_queue)
        self.init_btn.setEnabled(False)
        root_row.addWidget(self.init_btn)
        layout.addLayout(root_row)

        # Stats banner
        self.stats_label = QLabel("not connected")
        self.stats_label.setObjectName("status")
        layout.addWidget(self.stats_label)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_queue_tab(), "Queue management")
        self.tabs.addTab(self._build_worker_tab(), "Worker control")
        layout.addWidget(self.tabs, stretch=1)

    def _build_queue_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        # 4 bucket lists in a grid
        grid = QHBoxLayout()
        self.pending_list = self._make_bucket_list("Pending")
        self.inprog_list = self._make_bucket_list("In progress")
        self.done_list = self._make_bucket_list("Done")
        self.failed_list = self._make_bucket_list("Failed")
        for label_text, list_w in [
            ("Pending", self.pending_list),
            ("In progress", self.inprog_list),
            ("Done", self.done_list),
            ("Failed", self.failed_list),
        ]:
            col = QVBoxLayout()
            col.addWidget(QLabel(label_text))
            col.addWidget(list_w)
            grid.addLayout(col)
        l.addLayout(grid)
        # Action buttons
        ctrl = QHBoxLayout()
        self.add_files_btn = QPushButton("Add files…")
        self.add_files_btn.clicked.connect(self._add_files)
        self.add_files_btn.setEnabled(False)
        ctrl.addWidget(self.add_files_btn)
        self.reset_stale_btn = QPushButton("Reset stale")
        self.reset_stale_btn.clicked.connect(self._reset_stale)
        self.reset_stale_btn.setEnabled(False)
        ctrl.addWidget(self.reset_stale_btn)
        ctrl.addStretch(1)
        l.addLayout(ctrl)
        return w

    def _build_worker_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        # Worker config
        cfg = QHBoxLayout()
        cfg.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        cfg.addWidget(self.profile_combo, stretch=1)
        cfg.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        cfg.addWidget(self.encoder_selector, stretch=1)
        cfg.addWidget(QLabel("Workers:"))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(2)
        cfg.addWidget(self.workers_spin)
        l.addLayout(cfg)
        # Out dir
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output dir:"))
        self.out_label = QLabel("(none)")
        self.out_label.setObjectName("path")
        out_row.addWidget(self.out_label, stretch=1)
        self.pick_out_btn = QPushButton("Browse…")
        self.pick_out_btn.clicked.connect(self._pick_out)
        out_row.addWidget(self.pick_out_btn)
        l.addLayout(out_row)
        # Start/stop
        ctrl = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start worker")
        self.start_btn.setObjectName("run")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_worker)
        ctrl.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("cancel")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_worker)
        ctrl.addWidget(self.stop_btn)
        ctrl.addStretch(1)
        l.addLayout(ctrl)
        # Log
        self.worker_log = LogConsole()
        l.addWidget(self.worker_log)
        return w

    def _make_bucket_list(self, label: str) -> QListWidget:
        lw = QListWidget()
        return lw

    # event handlers
    def _pick_root(self) -> None: ...
    def _init_queue(self) -> None: ...
    def _add_files(self) -> None: ...
    def _reset_stale(self) -> None: ...
    def _pick_out(self) -> None: ...
    def _start_worker(self) -> None: ...
    def _stop_worker(self) -> None: ...
    def _on_stats(self, s: dict) -> None:
        self.stats_label.setText(
            f"pending: {s['pending']}   in_progress: {s['in_progress']}   "
            f"done: {s['done']}   failed: {s['failed']}"
        )
        # Optionally refresh per-bucket lists by enumerating files.
```

## Workitem 4 — `GenerateVariantsWorker`

**File:** `src/yt_uniquifier/gui/workers/generate_variants_worker.py` (new)

```python
"""Generate N variants of a source for the validation harness.

Equivalent to tools/generate_variants.py from Spec 18 (v0.4.1), but as a
GUI-controlled worker so progress streams to the screen.
"""

from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.gui.workers.base import WorkerBase

# Reuse the VariantRecord dataclass from tools/generate_variants.py if it
# defines one; else inline a tiny equivalent here.


class GenerateVariantsWorker(WorkerBase):
    """Generate N variants + write manifest.json to out_dir."""

    variant_done = pyqtSignal(dict)              # VariantRecord as dict

    def __init__(
        self,
        source: Path,
        profile: Profile,
        out_dir: Path,
        n: int,
        *,
        encoder_override: str | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.profile = profile
        self.out_dir = out_dir
        self.n = n
        self.encoder_override = encoder_override

    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict] = []
        for i in range(1, self.n + 1):
            if self.cancel_token.is_cancelled():
                self.log.emit(f"cancelled after {i - 1}/{self.n}")
                break
            try:
                plan = build_plan(self.source, self.profile, self.encoder_override)
                out = self.out_dir / f"variant_{i:03d}.mp4"
                work = self.out_dir / "work" / f"variant_{i:03d}"
                run_full(plan, RunOptions(
                    work_dir=work, output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                ), cancel_token=self.cancel_token)
                # Load qa.json to fill predicted metrics
                qa_path = out.with_suffix(out.suffix + ".qa.json")
                qa = json.loads(qa_path.read_text()) if qa_path.exists() else {}
                worst = max(
                    (c.get("combined", 0.0) for c in qa.get("chunk_similarities", [])),
                    default=None,
                )
                rec = {
                    "variant_id": f"variant_{i:03d}",
                    "output_path": str(out),
                    "qa_json_path": str(qa_path),
                    "run_seed": plan.run_seed,
                    "plan_hash": plan.plan_hash,
                    "cid_predict_self": qa.get("cid_predict_self"),
                    "audio_fp_hamming_per_frame": qa.get("audio_fp_hamming_per_frame"),
                    "phash_worst_chunk": worst,
                    "vmaf_mean": qa.get("vmaf_mean"),
                }
                records.append(rec)
                self.variant_done.emit(rec)
                self.progress.emit(i / self.n, f"{i} / {self.n}")
            except Exception as exc:
                self.log.emit(f"variant {i}: FAILED — {exc}")

        manifest = {
            "source": str(self.source),
            "profile": self.profile.name,
            "encoder": self.encoder_override,
            "n_variants": len(records),
            "variants": records,
        }
        (self.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        self.finished_ok.emit(manifest)
```

## Workitem 5 — `CorrelationWorker`

**File:** `src/yt_uniquifier/gui/workers/correlation_worker.py` (new)

Reuses the Spearman implementation from
`tools/validation_correlate.py` (v0.4.1). Either subprocess-invoke that
script, or import the function directly if v0.4.1 makes it importable.

```python
"""Runs the Spearman correlation analysis from tools/validation_correlate.py."""

from __future__ import annotations
import csv
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.gui.workers.base import WorkerBase


class CorrelationWorker(WorkerBase):
    """Returns a report dict for the validation screen to render."""

    report_ready = pyqtSignal(dict)

    def __init__(self, csv_path: Path) -> None:
        super().__init__()
        self.csv_path = csv_path

    def run(self) -> None:
        # Preferred: call the script directly via subprocess (script must
        # be on PATH or use full path to tools/validation_correlate.py).
        tool = Path(__file__).parents[4] / "tools" / "validation_correlate.py"
        if not tool.exists():
            self.failed.emit("tools/validation_correlate.py not found — install v0.4.1 first")
            return
        try:
            proc = subprocess.run(
                [sys.executable, str(tool), str(self.csv_path)],
                capture_output=True, text=True, check=True, timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            self.failed.emit(f"correlation failed: {exc.stderr.strip()[-500:]}")
            return
        # Parse stdout text into a dict. Script output is human-readable,
        # so we capture verbatim and let the screen display it.
        self.report_ready.emit({"text": proc.stdout})
        self.finished_ok.emit({"text": proc.stdout})
```

## Workitem 6 — Validation screen

**File:** `src/yt_uniquifier/gui/screens/validation.py` (new)

```python
"""3-step wizard for real-CID validation harness."""

from __future__ import annotations
import csv
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.correlation_worker import CorrelationWorker
from yt_uniquifier.gui.workers.generate_variants_worker import GenerateVariantsWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"
DEFAULT_CSV = Path(__file__).parents[4] / "tools" / "validation_log.csv"

STATUS_OPTIONS = ["", "no_match", "match", "pending", "removed", "error"]


class ValidationScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.manifest: dict | None = None
        self.gen_worker: GenerateVariantsWorker | None = None
        self.corr_worker: CorrelationWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Step indicator
        self.step_label = QLabel("<h3>Step 1 of 3 — Generate variants</h3>")
        layout.addWidget(self.step_label)

        # Stacked panels for 3 steps
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_generate_step())
        self.stack.addWidget(self._build_record_step())
        self.stack.addWidget(self._build_analyze_step())
        layout.addWidget(self.stack, stretch=1)

        # Nav
        nav = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self._go_back)
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.next_btn)
        layout.addLayout(nav)

    # ----- Step 1: generate -----
    def _build_generate_step(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        self.gen_picker = FilePickerRow("Source:", "input",
                                        "*.mp4 *.mov *.mkv", self.state)
        l.addWidget(self.gen_picker)
        row = QHBoxLayout()
        row.addWidget(QLabel("Profile:"))
        self.gen_profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.gen_profile_combo.addItem(p.stem, str(p))
        row.addWidget(self.gen_profile_combo, stretch=1)
        row.addWidget(QLabel("Encoder:"))
        self.gen_encoder = EncoderSelector(self.state)
        row.addWidget(self.gen_encoder, stretch=1)
        row.addWidget(QLabel("N:"))
        self.gen_n_spin = QSpinBox()
        self.gen_n_spin.setRange(1, 50)
        self.gen_n_spin.setValue(10)
        row.addWidget(self.gen_n_spin)
        l.addLayout(row)
        # Out dir
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Out dir:"))
        self.gen_out_label = QLabel("(none)")
        self.gen_out_label.setObjectName("path")
        out_row.addWidget(self.gen_out_label, stretch=1)
        pick_btn = QPushButton("Browse…")
        pick_btn.clicked.connect(self._pick_gen_out)
        out_row.addWidget(pick_btn)
        l.addLayout(out_row)
        # Action
        self.gen_btn = QPushButton("▶ Generate")
        self.gen_btn.setObjectName("run")
        self.gen_btn.clicked.connect(self._on_generate)
        l.addWidget(self.gen_btn)
        # Log
        self.gen_log = LogConsole()
        l.addWidget(self.gen_log)
        return w

    # ----- Step 2: record -----
    def _build_record_step(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel(
            "Upload each variant as Unlisted on YouTube. After 5–10 min check "
            "Studio → Content → Copyright. Record per-variant outcomes below."
        ))
        self.record_table = QTableWidget(0, 8)
        self.record_table.setHorizontalHeaderLabels([
            "variant_id", "cid_predict", "phash_worst", "audio_hamming",
            "upload_date", "youtube_video_id", "match_status", "notes",
        ])
        l.addWidget(self.record_table)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_csv_btn = QPushButton("Save to validation_log.csv")
        self.save_csv_btn.clicked.connect(self._save_csv)
        save_row.addWidget(self.save_csv_btn)
        l.addLayout(save_row)
        return w

    # ----- Step 3: analyze -----
    def _build_analyze_step(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel(f"CSV path: <code>{DEFAULT_CSV}</code>"))
        self.run_corr_btn = QPushButton("▶ Run correlation analysis")
        self.run_corr_btn.setObjectName("run")
        self.run_corr_btn.clicked.connect(self._on_correlate)
        l.addWidget(self.run_corr_btn)
        self.corr_output = QPlainTextEdit()
        self.corr_output.setReadOnly(True)
        l.addWidget(self.corr_output, stretch=1)
        return w

    # event handlers
    def _go_back(self) -> None:
        idx = max(0, self.stack.currentIndex() - 1)
        self.stack.setCurrentIndex(idx)
        self._refresh_step_label(idx)

    def _go_next(self) -> None:
        idx = min(2, self.stack.currentIndex() + 1)
        self.stack.setCurrentIndex(idx)
        self._refresh_step_label(idx)

    def _refresh_step_label(self, idx: int) -> None:
        titles = ["Generate variants", "Record real outcomes", "Analyze correlations"]
        self.step_label.setText(f"<h3>Step {idx + 1} of 3 — {titles[idx]}</h3>")
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < 2)

    def _pick_gen_out(self) -> None: ...
    def _on_generate(self) -> None: ...
    def _on_variant_done(self, rec: dict) -> None: ...
    def _save_csv(self) -> None:
        # Append rows from table to validation_log.csv.
        DEFAULT_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not DEFAULT_CSV.exists()
        with DEFAULT_CSV.open("a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "variant_id", "source_basename", "profile", "output_path",
                    "run_seed", "plan_hash", "cid_predict_self",
                    "audio_fp_hamming_per_frame", "phash_worst_chunk", "vmaf_mean",
                    "upload_date", "youtube_video_id", "match_status",
                    "matched_against", "claim_type", "notes",
                ])
            # ... iterate rows in self.record_table, write each
        QMessageBox.information(self, "Saved", f"Appended to {DEFAULT_CSV}")

    def _on_correlate(self) -> None:
        self.corr_worker = CorrelationWorker(DEFAULT_CSV)
        self.corr_worker.report_ready.connect(
            lambda d: self.corr_output.setPlainText(d["text"])
        )
        self.corr_worker.failed.connect(
            lambda msg: self.corr_output.setPlainText(f"FAILED: {msg}")
        )
        self.corr_worker.start()
```

**Tests:** `tests/unit/test_gui_validation_screen.py`
- `test_step_navigation` — clicking Next/Back changes stack index
- `test_save_csv_appends` — populate record table → save → CSV file has expected rows
- `test_correlation_loads_into_output` — mock worker → corr_output populated

## Workitem 7 — Sidebar registration

**File:** `src/yt_uniquifier/gui/app_pyqt.py` — update:

```python
from yt_uniquifier.gui.screens.queue import QueueScreen
from yt_uniquifier.gui.screens.validation import ValidationScreen

SIDEBAR_ITEMS = [
    ("Run",            RunScreen),
    ("Batch",          BatchScreen),
    ("Calibrate",      CalibrateScreen),
    ("QA Viewer",      QaViewerScreen),
    ("Profile Editor", ProfileEditorScreen),
    ("History",        HistoryScreen),
    ("Corpus",         lambda s: PlaceholderScreen("Corpus", "v0.5.4")),
    ("Queue",          QueueScreen),                    # was placeholder
    ("Validation",     ValidationScreen),               # was placeholder
    ("Settings",       lambda s: PlaceholderScreen("Settings", "v0.5.4")),
]
```

## Acceptance

```bash
# 1. Launch.
yt-uniq-gui

# 2. Queue screen → Browse to /tmp/q → Init → 4 buckets visible with 0 items.
# 3. Add 2 mp4 files → pending shows 2.
# 4. Worker tab → pick profile + out dir → Start → log shows lease/run/release events.
# 5. After both done → pending=0, done=2.

# 6. Validation screen → Step 1: source + profile + N=3 + out dir → Generate.
# 7. Watch 3 variants generate. Manifest appears at out_dir/manifest.json.
# 8. Click Next → Step 2: record table populated with 3 rows + their KPIs.
# 9. Manually fill upload_date / video_id / match_status for one row → Save CSV.
# 10. Click Next → Step 3 → Run correlation → output area shows Spearman or "≥5 samples needed".

# 11. Tests.
pytest -q tests/unit/test_gui_queue_* tests/unit/test_gui_validation_*
ruff check . && mypy src/yt_uniquifier/gui
```

## Tests

| Уровень | Файл | Кол-во |
|---|---|---|
| Unit | `test_gui_queue_status_worker.py` | 3 |
| Unit | `test_gui_queue_worker.py` | 4 |
| Unit | `test_gui_validation_screen.py` | 3 |
| **Total** | | **~10 new** |

## Risks

| Риск | Митигация |
|---|---|
| `QueueStatusWorker` poll loop holds the thread spinning | Sleep in 100ms slices for responsive cancel; `daemon`-like behavior via cancel_token |
| Worker tab "Start worker" runs in foreground = blocks GUI from accepting more clicks | QueueWorker is QThread, runs off main thread — main loop stays responsive |
| `tools/validation_correlate.py` not yet shipped (v0.4.1 not landed) | CorrelationWorker emits failed signal with clear message; Validation screen still allows Step 1/2 (generate + record); Step 3 explicitly disabled with tooltip |
| Long path in queue bucket lists overflows | Truncate display (basename only); full path in tooltip |
| CSV append corrupts if user edits file externally | `csv.writer` with append mode is safe; warn if file has wrong header |
| Multiple workers on one machine each call `heartbeat()` independently | Existing `FileQueue.heartbeat` uses per-process unique alive-file name; no collision |

## Hand-off

После v0.5.3:

- 8 functional screens (+ Queue + Validation).
- 2 placeholders: Corpus, Settings.
- Validation harness fully wrapped в UI (assumes v0.4.1 tools shipped).
- Distributed queue dashboard работает.
- ~10 новых тестов.

Tag: `v0.5.3`.

## Effort

| Item | Time |
|---|---|
| 1. `QueueStatusWorker` + 3 tests | 1.5 hours |
| 2. `QueueWorker` + 4 tests | 2 hours |
| 3. Queue screen (2 sub-tabs) | 2.5 hours |
| 4. `GenerateVariantsWorker` | 1 hour |
| 5. `CorrelationWorker` | 30 min |
| 6. Validation screen (3-step wizard) | 3 hours |
| 7. Validation screen tests (3) | 1 hour |
| 8. Sidebar wiring + commit + tag | 30 min |
| 9. Cross-screen smoke (manual) | 30 min |
| **Total** | **~12.5 hours / ~3 working days** |
