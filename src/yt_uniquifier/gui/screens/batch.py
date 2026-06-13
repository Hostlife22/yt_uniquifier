"""Batch — directory of files iterated through run_full."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.workers.batch_worker import BatchWorker

PROFILES_DIR = profiles_dir()


class BatchScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.input_dir: Path | None = None
        self.output_dir: Path | None = None
        self.worker: BatchWorker | None = None
        self._row_index: dict[str, int] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Batch")
        title.setObjectName("title")
        layout.addWidget(title)

        # Input dir
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Input directory:"))
        self.input_label = QLabel("(none)")
        self.input_label.setObjectName("path")
        row1.addWidget(self.input_label, stretch=1)
        b1 = QPushButton("Browse…")
        b1.clicked.connect(self._pick_input)
        row1.addWidget(b1)
        layout.addLayout(row1)

        # Output dir
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Output directory:"))
        self.output_label = QLabel("(none)")
        self.output_label.setObjectName("path")
        row2.addWidget(self.output_label, stretch=1)
        b2 = QPushButton("Browse…")
        b2.clicked.connect(self._pick_output)
        row2.addWidget(b2)
        layout.addLayout(row2)

        # Pattern + profile + encoder + continue-on-error
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Pattern:"))
        self.pattern_edit = QLineEdit("*.mp4")
        self.pattern_edit.textChanged.connect(self._refresh_preview)
        row3.addWidget(self.pattern_edit)
        row3.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        row3.addWidget(self.profile_combo, stretch=1)
        row3.addWidget(QLabel("Encoder:"))
        self.encoder_selector = EncoderSelector(self.state)
        row3.addWidget(self.encoder_selector, stretch=1)
        self.continue_check = QCheckBox("Continue on error")
        self.continue_check.setChecked(True)
        row3.addWidget(self.continue_check)
        layout.addLayout(row3)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["File", "Status", "Output", "Notes"],
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        layout.addWidget(self.table, stretch=1)

        # Overall progress bar (X of N files complete; updated on file_done)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("batch_progress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Files: %v / %m (%p%)")
        layout.addWidget(self.progress_bar)

        # Controls
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
        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        controls.addWidget(self.status_label)
        controls.addStretch(1)
        layout.addLayout(controls)

    # ---- handlers ----
    def _pick_input(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Input directory")
        if d:
            self.input_dir = Path(d)
            self.input_label.setText(d)
            self._refresh_preview()
            self._refresh_run_btn()

    def _pick_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output directory")
        if d:
            self.output_dir = Path(d)
            self.output_label.setText(d)
            self._refresh_run_btn()

    def _refresh_preview(self) -> None:
        self.table.setRowCount(0)
        self._row_index = {}
        if self.input_dir is None:
            return
        for src in sorted(self.input_dir.glob(self.pattern_edit.text() or "*.mp4")):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(src.name))
            self.table.setItem(r, 1, QTableWidgetItem("pending"))
            self.table.setItem(r, 2, QTableWidgetItem(""))
            self.table.setItem(r, 3, QTableWidgetItem(""))
            self._row_index[str(src)] = r
        self.status_label.setText(f"{self.table.rowCount()} file(s) matched")

    def _refresh_run_btn(self) -> None:
        ready = (
            self.input_dir is not None
            and self.output_dir is not None
            and self.worker is None
            and self.table.rowCount() > 0
        )
        self.run_btn.setEnabled(ready)

    def _on_run(self) -> None:
        if self.input_dir is None or self.output_dir is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Profile error", str(exc))
            return

        self.worker = BatchWorker(
            self.input_dir,
            self.output_dir,
            profile,
            self.encoder_selector.currentData(),
            glob_pattern=self.pattern_edit.text() or "*.mp4",
            continue_on_error=self.continue_check.isChecked(),
        )
        self.worker.file_started.connect(
            lambda p: self._set_status(p, "running"),
        )
        self.worker.file_done.connect(
            lambda p, out: self._set_status(p, "done", out=out),
        )
        self.worker.file_failed.connect(
            lambda p, msg: self._set_status(p, "failed", note=msg),
        )
        self.worker.progress.connect(
            lambda _f, m: self.status_label.setText(m),
        )
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("starting…")
        # Reset overall progress: max = number of files matched in the table.
        self.progress_bar.setRange(0, max(self.table.rowCount(), 1))
        self.progress_bar.setValue(0)
        self.worker.start()

    def _on_cancel(self) -> None:
        if self.worker is not None:
            self.worker.request_cancel()
            self.status_label.setText("cancelling…")

    def _on_done(self, _payload: object) -> None:
        self.status_label.setText("Done.")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self._refresh_run_btn()

    def _on_failed(self, msg: str) -> None:
        self.status_label.setText(f"FAILED: {msg}")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self._refresh_run_btn()

    def _drop_worker(self) -> None:
        """Join the BatchWorker QThread before dropping the Python ref.

        Qt delivers finished_ok / failed via QueuedConnection while the
        thread's run() may still be unwinding. Setting ``self.worker = None``
        without first quit()+wait() can leave a live C++ QThread behind
        the dropped Python reference, causing a "QThread: Destroyed
        while thread is still running" abort.
        """
        if self.worker is None:
            return
        self.worker.quit()
        self.worker.wait(1000)
        self.worker = None

    def _set_status(
        self, path: str, status: str, *, out: str = "", note: str = "",
    ) -> None:
        row = self._row_index.get(path)
        if row is None:
            return
        self.table.setItem(row, 1, QTableWidgetItem(status))
        if out:
            self.table.setItem(row, 2, QTableWidgetItem(out))
        if note:
            self.table.setItem(row, 3, QTableWidgetItem(note))
        # Each transition to a terminal status advances the overall bar.
        if status in {"done", "failed"}:
            self.progress_bar.setValue(self.progress_bar.value() + 1)
