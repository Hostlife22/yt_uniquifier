"""PyQt6 main window. Thin shell over `core/orchestrator` via `Worker`."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyQt6 is not installed. Install with: pip install 'yt-uniquifier[gui]'"
    ) from exc

from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.orchestrator import RunOptions, build_plan
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.gui.worker import Worker

PROFILES_DIR = Path(__file__).parents[1] / "profiles"

QSS = """
QWidget { background: #1f1f2b; color: #e2e2e8; font-size: 13px; }
QPushButton {
    background: #3b6ea8; color: white; padding: 6px 14px;
    border-radius: 4px; border: none;
}
QPushButton:disabled { background: #555; color: #aaa; }
QPushButton:hover:!disabled { background: #4f87c4; }
QPushButton#cancel { background: #a83b3b; }
QPushButton#cancel:hover { background: #c44f4f; }
QPushButton#run { background: #d18b3b; font-weight: bold; padding: 8px 18px; }
QLabel#path { background: #2a2a37; padding: 5px 8px; border-radius: 3px; }
QComboBox { background: #2a2a37; padding: 4px 8px; border-radius: 3px; border: 1px solid #444; }
QProgressBar {
    background: #2a2a37; border-radius: 3px; text-align: center;
    color: white; padding: 1px;
}
QProgressBar::chunk { background: #3b6ea8; border-radius: 3px; }
QTextEdit {
    background: #16161e; color: #c8c8d0;
    font-family: SFMono-Regular, Menlo, monospace; font-size: 11px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("yt-uniquifier")
        self.resize(720, 520)
        self.setStyleSheet(QSS)

        self.input_path: Path | None = None
        self.output_path: Path | None = None
        self.qa_html: Path | None = None
        self.worker: Worker | None = None

        self._build_ui()
        self._refresh_inputs_state()

    # -------- UI scaffolding --------

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        layout.addLayout(self._file_row("Input video:", "input"))
        layout.addLayout(self._file_row("Output:", "output", save=True))

        # Profile + encoder row.
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        opts.addWidget(self.profile_combo, stretch=1)

        opts.addWidget(QLabel("Encoder:"))
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItem("auto", None)
        try:
            for cand in detect_encoders():
                label = cand.name + ("" if cand.works else " (unavailable)")
                self.encoder_combo.addItem(label, cand.name if cand.works else None)
        except YtUniquifierError as exc:
            self._log(f"encoder detection failed: {exc}")
        opts.addWidget(self.encoder_combo, stretch=1)
        layout.addLayout(opts)

        # Run + cancel.
        controls = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("run")
        self.run_btn.clicked.connect(self._on_run)
        controls.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        controls.addWidget(self.cancel_btn)

        self.report_btn = QPushButton("Open QA report")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_open_report)
        controls.addWidget(self.report_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        # Progress + status.
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        layout.addWidget(self.progress)
        self.status = QLabel("Idle.")
        layout.addWidget(self.status)

        # Log.
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

        self.setCentralWidget(root)

    def _file_row(self, label: str, kind: str, *, save: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        path_lbl = QLabel("(not selected)")
        path_lbl.setObjectName("path")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(path_lbl, stretch=1)
        btn = QPushButton("Browse…")

        def _on_click(
            _checked: bool = False, k: str = kind, lab: QLabel = path_lbl, s: bool = save,
        ) -> None:
            self._pick(k, lab, s)

        btn.clicked.connect(_on_click)
        row.addWidget(btn)
        setattr(self, f"_{kind}_label", path_lbl)
        return row

    # -------- actions --------

    def _pick(self, kind: str, label: QLabel, save: bool) -> None:
        if save:
            path, _ = QFileDialog.getSaveFileName(
                self, "Output file", "", "MP4 (*.mp4)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Input video", "",
                "Video files (*.mp4 *.mov *.mkv *.webm);;All files (*)",
            )
        if not path:
            return
        p = Path(path)
        if kind == "input":
            self.input_path = p
        else:
            self.output_path = p
        label.setText(str(p))
        self._refresh_inputs_state()

    def _refresh_inputs_state(self) -> None:
        ready = self.input_path is not None and self.output_path is not None
        self.run_btn.setEnabled(ready and self.worker is None)

    def _on_run(self) -> None:
        if self.input_path is None or self.output_path is None:
            return
        try:
            profile = load_profile(Path(self.profile_combo.currentData()))
            enc_override = self.encoder_combo.currentData()
            plan = build_plan(self.input_path, profile, enc_override)
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Plan error", str(exc))
            return

        work_dir = Path.home() / ".cache" / "yt_uniquifier" / "work" / plan.plan_hash
        options = RunOptions(work_dir=work_dir, output=self.output_path,
                             keep_segments=False, enforce_preflight=True)

        self.worker = Worker(plan, options, run_qa=True, fast_qa=False)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._log)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.report_btn.setEnabled(False)
        self.qa_html = None
        self.status.setText(f"Running on {plan.encoder.name} ({plan.encoder.vendor})…")
        self.log.append(f"--- Run started: plan_hash={plan.plan_hash} ---")
        self.worker.start()

    def _on_cancel(self) -> None:
        if self.worker is not None:
            self.worker.request_cancel()
            self.status.setText("Cancelling…")

    def _on_open_report(self) -> None:
        if self.qa_html and self.qa_html.exists():
            webbrowser.open(self.qa_html.as_uri())

    # -------- worker callbacks --------

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.status.setText(message)

    def _log(self, line: str) -> None:
        self.log.append(line)

    def _on_done(self, output: str, qa_html: str) -> None:
        self.status.setText(f"Done: {output}")
        self.progress.setValue(1000)
        if qa_html:
            self.qa_html = Path(qa_html)
            self.report_btn.setEnabled(True)
        self.worker = None
        self.cancel_btn.setEnabled(False)
        self._refresh_inputs_state()

    def _on_failed(self, message: str) -> None:
        self.status.setText("Failed.")
        self._log(f"!!! {message}")
        self.worker = None
        self.cancel_btn.setEnabled(False)
        self._refresh_inputs_state()


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    main()
