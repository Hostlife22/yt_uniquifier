"""Queue / Worker dashboard — distributed queue management + drainer control."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.queue_io_worker import QueueIoWorker
from yt_uniquifier.gui.workers.queue_status_worker import QueueStatusWorker
from yt_uniquifier.gui.workers.queue_worker import QueueWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class QueueScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.queue_root: Path | None = None
        self.out_dir: Path | None = None
        self.status_worker: QueueStatusWorker | None = None
        self.drain_worker: QueueWorker | None = None
        self.io_worker: QueueIoWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Queue / Worker")
        title.setObjectName("title")
        layout.addWidget(title)

        # Queue root + init
        row = QHBoxLayout()
        row.addWidget(QLabel("Queue root:"))
        self.root_label = QLabel("(none)")
        self.root_label.setObjectName("path")
        row.addWidget(self.root_label, stretch=1)
        self.pick_root_btn = QPushButton("Browse…")
        self.pick_root_btn.clicked.connect(self._pick_root)
        row.addWidget(self.pick_root_btn)
        self.init_btn = QPushButton("Init queue here")
        self.init_btn.setEnabled(False)
        self.init_btn.clicked.connect(self._init_queue)
        row.addWidget(self.init_btn)
        layout.addLayout(row)

        # Stats banner
        self.stats_label = QLabel("not connected")
        self.stats_label.setObjectName("status")
        layout.addWidget(self.stats_label)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_manage_tab(), "Queue management")
        self.tabs.addTab(self._build_worker_tab(), "Worker control")
        layout.addWidget(self.tabs, stretch=1)

    def _build_manage_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        actions = QHBoxLayout()
        self.add_files_btn = QPushButton("Add files…")
        self.add_files_btn.setEnabled(False)
        self.add_files_btn.clicked.connect(self._add_files)
        actions.addWidget(self.add_files_btn)
        self.reset_stale_btn = QPushButton("Reset stale (>5 min)")
        self.reset_stale_btn.setEnabled(False)
        self.reset_stale_btn.clicked.connect(self._reset_stale)
        actions.addWidget(self.reset_stale_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addWidget(QLabel(
            "Buckets live on the shared filesystem — refresh updates every 2s "
            "automatically once a queue root is set.",
        ))
        layout.addStretch(1)
        return w

    def _build_worker_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

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
        layout.addLayout(cfg)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output dir:"))
        self.out_label = QLabel("(none)")
        self.out_label.setObjectName("path")
        out_row.addWidget(self.out_label, stretch=1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._pick_out)
        out_row.addWidget(b)
        layout.addLayout(out_row)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start worker")
        self.start_btn.setObjectName("run")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_worker)
        controls.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("cancel")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_worker)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.worker_log = LogConsole()
        layout.addWidget(self.worker_log, stretch=1)
        return w

    # ---- handlers ----
    def _pick_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Queue root")
        if not d:
            return
        self.queue_root = Path(d)
        self.root_label.setText(d)
        self.init_btn.setEnabled(True)
        self.add_files_btn.setEnabled(True)
        self.reset_stale_btn.setEnabled(True)
        self._start_status_poller()
        self._refresh_start_btn()

    def _init_queue(self) -> None:
        if self.queue_root is None or self.io_worker is not None:
            return
        self._run_io_worker("init", success_title="Initialised")

    def _add_files(self) -> None:
        if self.queue_root is None or self.io_worker is not None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files to queue", "",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
        )
        if not files:
            return
        self._run_io_worker(
            "add", files=[Path(f) for f in files], log_to_worker_log=True,
        )

    def _reset_stale(self) -> None:
        if self.queue_root is None or self.io_worker is not None:
            return
        self._run_io_worker("reset_stale", success_title="Reset")

    def _run_io_worker(
        self,
        op: str,
        *,
        files: list[Path] | None = None,
        success_title: str | None = None,
        log_to_worker_log: bool = False,
    ) -> None:
        """Common dispatcher for queue FS ops. Keeps the GUI thread free."""
        assert self.queue_root is not None
        # All op signatures are tight Literals on QueueIoWorker; cast at
        # the boundary so the type system is happy.
        from typing import cast

        from yt_uniquifier.gui.workers.queue_io_worker import Op as _Op
        self.io_worker = QueueIoWorker(
            self.queue_root, cast(_Op, op), files=files,
        )
        self.io_worker.done.connect(
            lambda n, msg: self._on_io_done(n, msg, success_title, log_to_worker_log),
        )
        self.io_worker.failed.connect(self._on_io_failed)
        self.io_worker.start()

    def _on_io_done(
        self,
        count: int,
        msg: str,
        success_title: str | None,
        log_to_worker_log: bool,
    ) -> None:
        if log_to_worker_log:
            self.worker_log.log(msg, "info")
        elif success_title is not None:
            QMessageBox.information(self, success_title, msg)
        self._drop_io_worker()

    def _on_io_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Queue operation failed", msg)
        self._drop_io_worker()

    def _drop_io_worker(self) -> None:
        if self.io_worker is None:
            return
        self.io_worker.quit()
        self.io_worker.wait(500)
        self.io_worker = None

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output directory")
        if d:
            self.out_dir = Path(d)
            self.out_label.setText(d)
            self._refresh_start_btn()

    def _refresh_start_btn(self) -> None:
        ready = (
            self.queue_root is not None
            and self.out_dir is not None
            and self.drain_worker is None
        )
        self.start_btn.setEnabled(ready)

    def _start_status_poller(self) -> None:
        if self.queue_root is None:
            return
        if self.status_worker is not None:
            # quit() posts QThread.exit so the run-loop unblocks even if
            # cancel_token alone hadn't elapsed within wait(500). Without
            # this the wait() silently returns False and a live C++
            # thread is left behind the dropped Python ref.
            self.status_worker.request_cancel()
            self.status_worker.quit()
            self.status_worker.wait(500)
        self.status_worker = QueueStatusWorker(self.queue_root)
        self.status_worker.stats.connect(self._on_stats)
        self.status_worker.failed.connect(
            lambda msg: self.stats_label.setText(f"stats: {msg}"),
        )
        self.status_worker.start()

    @pyqtSlot(dict)
    def _on_stats(self, s: dict[str, int]) -> None:
        self.stats_label.setText(
            f"pending: {s.get('pending', '?')}   "
            f"in_progress: {s.get('in_progress', '?')}   "
            f"done: {s.get('done', '?')}   "
            f"failed: {s.get('failed', '?')}",
        )

    def _start_worker(self) -> None:
        if self.queue_root is None or self.out_dir is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Profile error", str(exc))
            return
        self.drain_worker = QueueWorker(
            self.queue_root, profile, self.out_dir,
            encoder_override=self.encoder_selector.currentData(),
            workers=self.workers_spin.value(),
        )
        self.drain_worker.lease_acquired.connect(
            lambda p: self.worker_log.log(f"leased: {p}", "info"),
        )
        self.drain_worker.file_done.connect(
            lambda p, o: self.worker_log.log(f"done: {p} → {o}", "info"),
        )
        self.drain_worker.file_failed.connect(
            lambda p, e: self.worker_log.log(f"FAILED: {p}: {e}", "error"),
        )
        self.drain_worker.finished_ok.connect(
            lambda _r: self._worker_done(),
        )
        self.drain_worker.failed.connect(
            lambda msg: self.worker_log.log(f"WORKER FAILED: {msg}", "error"),
        )
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker_log.log("worker started", "info")
        self.drain_worker.start()

    def _stop_worker(self) -> None:
        if self.drain_worker is not None:
            self.drain_worker.request_cancel()
            self.worker_log.log("stop requested…", "info")

    def _worker_done(self) -> None:
        self.worker_log.log("worker exited", "info")
        self.drain_worker = None
        self.stop_btn.setEnabled(False)
        self._refresh_start_btn()

    def on_show(self) -> None:
        # Restart status poller in case user navigated away and came back.
        if self.queue_root and (self.status_worker is None or not self.status_worker.isRunning()):
            self._start_status_poller()
