"""Validation — 3-step wizard for v0.4.1 real-CID validation harness."""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.encoder_selector import EncoderSelector
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.workers.correlate_worker import CorrelateWorker
from yt_uniquifier.gui.workers.generate_variants_worker import GenerateVariantsWorker

PROFILES_DIR = Path(__file__).parents[2] / "profiles"
REPO_ROOT = Path(__file__).parents[4]
DEFAULT_CSV = REPO_ROOT / "tools" / "validation_log.csv"
CORRELATE_TOOL = REPO_ROOT / "tools" / "validation_correlate.py"


class ValidationScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.manifest: dict[str, object] | None = None
        self.gen_worker: GenerateVariantsWorker | None = None
        self.corr_worker: CorrelateWorker | None = None
        self.input_path: Path | None = None
        self.gen_out_dir: Path | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Validation")
        title.setObjectName("title")
        layout.addWidget(title)

        self.step_label = QLabel("<b>Step 1 of 3 — Generate variants</b>")
        layout.addWidget(self.step_label)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_generate_step())
        self.stack.addWidget(self._build_record_step())
        self.stack.addWidget(self._build_analyze_step())
        layout.addWidget(self.stack, stretch=1)

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

    # ---- Step 1: generate ----
    def _build_generate_step(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.gen_picker = FilePickerRow(
            "Source:", "input",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
            self.state,
        )
        self.gen_picker.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.gen_picker)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile:"))
        self.gen_profile = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.gen_profile.addItem(p.stem, str(p))
        idx = self.gen_profile.findText("cid_aware")
        if idx >= 0:
            self.gen_profile.setCurrentIndex(idx)
        row.addWidget(self.gen_profile, stretch=1)
        row.addWidget(QLabel("Encoder:"))
        self.gen_encoder = EncoderSelector(self.state)
        row.addWidget(self.gen_encoder, stretch=1)
        row.addWidget(QLabel("N:"))
        self.gen_n = QSpinBox()
        self.gen_n.setRange(1, 50)
        self.gen_n.setValue(5)
        row.addWidget(self.gen_n)
        layout.addLayout(row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Out dir:"))
        self.gen_out_label = QLabel("(none)")
        self.gen_out_label.setObjectName("path")
        out_row.addWidget(self.gen_out_label, stretch=1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._pick_gen_out)
        out_row.addWidget(b)
        layout.addLayout(out_row)

        self.gen_btn = QPushButton("▶ Generate")
        self.gen_btn.setObjectName("run")
        self.gen_btn.setEnabled(False)
        self.gen_btn.clicked.connect(self._on_generate)
        layout.addWidget(self.gen_btn)

        self.gen_log = LogConsole()
        layout.addWidget(self.gen_log, stretch=1)
        return w

    # ---- Step 2: record ----
    def _build_record_step(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Upload each variant as Unlisted on YouTube. Wait 5–10 min, then "
            "check Studio → Content → Copyright. Record outcomes below.",
        ))
        self.record_table = QTableWidget(0, 8)
        self.record_table.setHorizontalHeaderLabels([
            "variant_id", "cid_predict", "phash_worst", "audio_hamming",
            "upload_date", "youtube_video_id", "match_status", "notes",
        ])
        layout.addWidget(self.record_table)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_csv_btn = QPushButton("Save to validation_log.csv")
        self.save_csv_btn.clicked.connect(self._save_csv)
        save_row.addWidget(self.save_csv_btn)
        layout.addLayout(save_row)
        return w

    # ---- Step 3: analyze ----
    def _build_analyze_step(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(f"CSV path: <code>{DEFAULT_CSV}</code>"))
        self.run_corr_btn = QPushButton("▶ Run correlation analysis")
        self.run_corr_btn.setObjectName("run")
        self.run_corr_btn.clicked.connect(self._on_correlate)
        layout.addWidget(self.run_corr_btn)
        self.corr_output = QPlainTextEdit()
        self.corr_output.setReadOnly(True)
        layout.addWidget(self.corr_output, stretch=1)
        return w

    # ---- nav ----
    def _go_back(self) -> None:
        idx = max(0, self.stack.currentIndex() - 1)
        self.stack.setCurrentIndex(idx)
        self._refresh_step_label(idx)

    def _go_next(self) -> None:
        idx = min(2, self.stack.currentIndex() + 1)
        self.stack.setCurrentIndex(idx)
        self._refresh_step_label(idx)

    def _refresh_step_label(self, idx: int) -> None:
        titles = [
            "Generate variants", "Record real outcomes", "Analyze correlations",
        ]
        self.step_label.setText(f"<b>Step {idx + 1} of 3 — {titles[idx]}</b>")
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < 2)

    # ---- handlers ----
    def _on_input_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.input_path = path
        self._refresh_gen_btn()

    def _pick_gen_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Variants out dir")
        if d:
            self.gen_out_dir = Path(d)
            self.gen_out_label.setText(d)
            self._refresh_gen_btn()

    def _refresh_gen_btn(self) -> None:
        ready = (
            self.input_path is not None
            and self.gen_out_dir is not None
            and self.gen_worker is None
        )
        self.gen_btn.setEnabled(ready)

    def _on_generate(self) -> None:
        if self.input_path is None or self.gen_out_dir is None:
            return
        try:
            profile = load_profile(Path(self.gen_profile.currentData()))
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Profile error", str(exc))
            return
        self.record_table.setRowCount(0)
        self.gen_worker = GenerateVariantsWorker(
            self.input_path, profile, self.gen_out_dir,
            n=self.gen_n.value(),
            encoder_override=self.gen_encoder.currentData(),
        )
        self.gen_worker.variant_done.connect(self._on_variant_done)
        self.gen_worker.progress.connect(
            lambda _f, m: self.gen_log.log(m, "info"),
        )
        self.gen_worker.finished_ok.connect(self._on_gen_finished)
        self.gen_worker.failed.connect(self._on_gen_failed)
        self.gen_btn.setEnabled(False)
        self.gen_log.log(f"generating {self.gen_n.value()} variants…", "info")
        self.gen_worker.start()

    def _on_gen_finished(self, _msg: object = "") -> None:
        """Clear gen_worker so the next Generate click is allowed."""
        self.gen_log.log("All variants generated.", "info")
        self._drop_gen_worker()
        self._refresh_gen_btn()

    def _on_gen_failed(self, msg: str) -> None:
        """Clear gen_worker on failure so the user can retry."""
        self.gen_log.log(f"FAILED: {msg}", "error")
        self._drop_gen_worker()
        self._refresh_gen_btn()

    def _drop_gen_worker(self) -> None:
        if self.gen_worker is None:
            return
        # Tests sometimes substitute a plain `object()` placeholder; only
        # call quit/wait on a real QThread.
        if hasattr(self.gen_worker, "quit"):
            self.gen_worker.quit()
        if hasattr(self.gen_worker, "wait"):
            self.gen_worker.wait(1000)
        self.gen_worker = None

    def _drop_corr_worker(self) -> None:
        if self.corr_worker is None:
            return
        if hasattr(self.corr_worker, "quit"):
            self.corr_worker.quit()
        if hasattr(self.corr_worker, "wait"):
            self.corr_worker.wait(1000)
        self.corr_worker = None

    def _on_variant_done(self, rec: dict[str, object]) -> None:
        r = self.record_table.rowCount()
        self.record_table.insertRow(r)
        self.record_table.setItem(r, 0, QTableWidgetItem(str(rec.get("variant_id", ""))))
        self.record_table.setItem(r, 1, QTableWidgetItem(
            f"{rec.get('cid_predict_self', '?')}",
        ))
        self.record_table.setItem(r, 2, QTableWidgetItem(
            f"{rec.get('phash_worst_chunk', '?')}",
        ))
        self.record_table.setItem(r, 3, QTableWidgetItem(
            f"{rec.get('audio_fp_hamming_per_frame', '?')}",
        ))
        # Editable cells for upload outcomes:
        for col in (4, 5, 6, 7):
            self.record_table.setItem(r, col, QTableWidgetItem(""))

    def _save_csv(self) -> None:
        if self.record_table.rowCount() == 0:
            QMessageBox.information(self, "Nothing to save", "Table is empty.")
            return
        DEFAULT_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not DEFAULT_CSV.exists()
        try:
            with DEFAULT_CSV.open("a", newline="") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow([
                        "variant_id", "source_basename", "profile",
                        "output_path", "run_seed", "plan_hash",
                        "cid_predict_self", "audio_fp_hamming_per_frame",
                        "phash_worst_chunk", "vmaf_mean",
                        "upload_date", "youtube_video_id", "match_status",
                        "matched_against", "claim_type", "notes",
                    ])
                source_name = (
                    Path(self.input_path).name if self.input_path else ""
                )
                profile_name = self.gen_profile.currentText()
                for r in range(self.record_table.rowCount()):
                    writer.writerow([
                        self._cell(r, 0),     # variant_id
                        source_name,
                        profile_name,
                        "",                    # output_path (not surfaced in UI)
                        "",                    # run_seed
                        "",                    # plan_hash
                        self._cell(r, 1),
                        self._cell(r, 3),
                        self._cell(r, 2),
                        "",                    # vmaf_mean
                        self._cell(r, 4),
                        self._cell(r, 5),
                        self._cell(r, 6),
                        "",                    # matched_against
                        "",                    # claim_type
                        self._cell(r, 7),
                    ])
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        QMessageBox.information(self, "Saved", f"Appended to {DEFAULT_CSV}")

    def _cell(self, row: int, col: int) -> str:
        item = self.record_table.item(row, col)
        return item.text() if item else ""

    def _on_correlate(self) -> None:
        if not CORRELATE_TOOL.exists():
            self.corr_output.setPlainText(
                f"tools/validation_correlate.py not found at {CORRELATE_TOOL}",
            )
            return
        if not DEFAULT_CSV.exists():
            self.corr_output.setPlainText(
                f"validation_log.csv not found at {DEFAULT_CSV}.\n"
                "Use Step 2 to save at least one row first.",
            )
            return
        if self.corr_worker is not None:
            return  # already running

        # Run the correlation script off the GUI thread — a blocking
        # subprocess.run(..., timeout=60) here previously froze the
        # window for up to a minute.
        self.run_corr_btn.setEnabled(False)
        self.corr_output.setPlainText("running…")
        self.corr_worker = CorrelateWorker(CORRELATE_TOOL, DEFAULT_CSV)
        self.corr_worker.correlated.connect(self._on_corr_done)
        self.corr_worker.failed.connect(self._on_corr_failed)
        self.corr_worker.start()

    def _on_corr_done(self, stdout: str) -> None:
        self.corr_output.setPlainText(stdout)
        self._drop_corr_worker()
        self.run_corr_btn.setEnabled(True)

    def _on_corr_failed(self, message: str) -> None:
        self.corr_output.setPlainText(message)
        self._drop_corr_worker()
        self.run_corr_btn.setEnabled(True)
