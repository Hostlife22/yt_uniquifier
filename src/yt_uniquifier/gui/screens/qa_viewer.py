"""QA Viewer — load existing .qa.html OR compute new on (input, output) pair."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

# Offscreen Qt platform crashes when QWebEngineView is instantiated.
# Force the label fallback when running headless (tests / CI).
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
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
        self.input_path: Path | None = None
        self.output_path: Path | None = None
        self.worker: QaWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("QA Viewer")
        title.setObjectName("title")
        layout.addWidget(title)

        # Mode tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_open_tab(), "Open existing")
        self.tabs.addTab(self._build_compute_tab(), "Compute new")
        layout.addWidget(self.tabs)

        # Viewer
        if HAS_WEBENGINE:
            self.viewer: QWidget = QWebEngineView()
        else:
            fallback = QLabel(
                "<p><i>PyQt6-WebEngine is not installed.</i></p>"
                "<p>Install it with:<br><code>pip install 'yt-uniquifier[gui]'</code></p>"
                "<p>Or click <b>Open in browser</b> below to view in your default browser.</p>",
            )
            fallback.setWordWrap(True)
            self.viewer = fallback
        layout.addWidget(self.viewer, stretch=1)

        # Footer
        footer = QHBoxLayout()
        self.open_browser_btn = QPushButton("Open in browser")
        self.open_browser_btn.setEnabled(False)
        self.open_browser_btn.clicked.connect(self._open_in_browser)
        footer.addWidget(self.open_browser_btn)
        footer.addStretch(1)
        layout.addLayout(footer)

    def _build_open_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("QA report (.qa.html):"))
        self.existing_label = QLabel("(none)")
        self.existing_label.setObjectName("path")
        row.addWidget(self.existing_label, stretch=1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._pick_existing)
        row.addWidget(b)
        layout.addLayout(row)
        layout.addStretch(1)
        return w

    def _build_compute_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.compute_input = FilePickerRow(
            "Input video:", "input",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
            self.state,
        )
        self.compute_input.path_changed.connect(self._on_input_changed)
        layout.addWidget(self.compute_input)

        self.compute_output = FilePickerRow(
            "Output video:", "output",
            "Video (*.mp4 *.mov *.mkv);;All (*)",
            self.state,
        )
        self.compute_output.path_changed.connect(self._on_output_changed)
        layout.addWidget(self.compute_output)

        controls = QHBoxLayout()
        self.compute_btn = QPushButton("Compute QA")
        self.compute_btn.setObjectName("run")
        self.compute_btn.setEnabled(False)
        self.compute_btn.clicked.connect(self._on_compute)
        controls.addWidget(self.compute_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        controls.addWidget(self.cancel_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        # Indeterminate spinner during compute — qa_worker exposes only
        # progress text, not a fraction, so we use range=(0,0) which Qt
        # renders as a marquee bar. Hidden until compute starts.
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("qa_progress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Computing QA…")
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.compute_log = LogConsole()
        layout.addWidget(self.compute_log, stretch=1)
        return w

    def _on_input_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.input_path = path
        self._refresh_compute_btn()

    def _on_output_changed(self, path: object) -> None:
        if path is not None and not isinstance(path, Path):
            return
        self.output_path = path
        self._refresh_compute_btn()

    def _refresh_compute_btn(self) -> None:
        ready = (
            self.input_path is not None
            and self.output_path is not None
            and self.worker is None
        )
        self.compute_btn.setEnabled(ready)

    def _pick_existing(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "QA HTML report", "",
            "QA report (*.qa.html);;HTML (*.html);;All (*)",
        )
        if path_str:
            self._load_html(Path(path_str))

    def _load_html(self, path: Path) -> None:
        self.qa_html_path = path
        self.existing_label.setText(str(path))
        self.open_browser_btn.setEnabled(True)
        if HAS_WEBENGINE and isinstance(self.viewer, QWebEngineView):
            self.viewer.load(QUrl.fromLocalFile(str(path)))

    def _on_compute(self) -> None:
        if self.input_path is None or self.output_path is None:
            QMessageBox.warning(self, "Inputs missing",
                                "Pick both input and output paths.")
            return
        self.worker = QaWorker(self.input_path, self.output_path)
        self.worker.progress.connect(
            lambda _f, m: self.compute_log.log(m, "info"),
        )
        self.worker.qa_ready.connect(
            lambda _j, h: self._load_html(Path(h)),
        )
        self.worker.failed.connect(self._on_failed)
        self.worker.finished_ok.connect(self._on_finished)
        self.compute_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.compute_log.log("computing QA…", "info")
        self.worker.start()

    def _on_cancel(self) -> None:
        if self.worker is not None:
            self.worker.request_cancel()

    def _on_failed(self, msg: str) -> None:
        self.compute_log.log(f"FAILED: {msg}", "error")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self._refresh_compute_btn()

    def _on_finished(self, _payload: object) -> None:
        self.compute_log.log("QA done.", "info")
        self._drop_worker()
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self._refresh_compute_btn()

    def _drop_worker(self) -> None:
        """Join the QaWorker QThread before dropping the Python ref."""
        if self.worker is None:
            return
        self.worker.quit()
        self.worker.wait(1000)
        self.worker = None

    def _open_in_browser(self) -> None:
        if self.qa_html_path and self.qa_html_path.exists():
            webbrowser.open(self.qa_html_path.as_uri())
