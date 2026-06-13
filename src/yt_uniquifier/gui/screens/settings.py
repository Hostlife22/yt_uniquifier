"""Settings — preferences with live theme switch."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState

PROFILES_DIR = profiles_dir()


class SettingsScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setObjectName("title")
        layout.addWidget(title)

        # Appearance
        appear = QGroupBox("Appearance")
        f = QFormLayout(appear)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light", "system"])
        self.theme_combo.setCurrentText(self.state.theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_change)
        mark(self.theme_combo, "Theme",
             "Switch between dark, light, and system color schemes.")
        f.addRow("Theme (live switch):", self.theme_combo)
        layout.addWidget(appear)

        # Defaults
        defaults = QGroupBox("Defaults")
        f2 = QFormLayout(defaults)
        self.default_profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.default_profile_combo.addItem(p.stem, str(p))
        if self.state.profile_path:
            idx = self.default_profile_combo.findData(str(self.state.profile_path))
            if idx >= 0:
                self.default_profile_combo.setCurrentIndex(idx)
        mark(self.default_profile_combo, "Default profile",
             "Profile pre-selected on Run / Batch / Calibrate screens at start.")
        f2.addRow("Default profile:", self.default_profile_combo)

        self.recents_cap_spin = QSpinBox()
        self.recents_cap_spin.setRange(5, 100)
        self.recents_cap_spin.setValue(20)
        self.recents_cap_spin.setEnabled(False)  # cap is module-level constant
        self.recents_cap_spin.setToolTip(
            "Hard-coded constant in v0.5.4; will be user-tunable in v0.6.",
        )
        mark(self.recents_cap_spin, "Recents cap",
             "Maximum number of recent files to remember (hard-coded in v0.5.4).")
        f2.addRow("Recents cap:", self.recents_cap_spin)

        self.history_cap_spin = QSpinBox()
        self.history_cap_spin.setRange(10, 1000)
        self.history_cap_spin.setValue(100)
        self.history_cap_spin.setEnabled(False)
        self.history_cap_spin.setToolTip(
            "Hard-coded constant in v0.5.4; will be user-tunable in v0.6.",
        )
        mark(self.history_cap_spin, "History cap",
             "Maximum number of past runs to keep in the History screen.")
        f2.addRow("History cap:", self.history_cap_spin)
        layout.addWidget(defaults)

        # Maintenance
        maint = QGroupBox("Maintenance")
        h = QHBoxLayout(maint)
        self.reset_enc_btn = QPushButton("&Reset encoder cache")
        self.reset_enc_btn.clicked.connect(self._reset_encoder_cache)
        mark(
            self.reset_enc_btn, "Reset encoder cache",
            "Delete the cached ffmpeg-encoder detection so they are re-probed on next run.",
        )
        h.addWidget(self.reset_enc_btn)
        self.open_logs_btn = QPushButton("Open &log dir")
        self.open_logs_btn.clicked.connect(self._open_log_dir)
        mark(self.open_logs_btn, "Open log directory",
             "Reveal the ~/.cache/yt_uniquifier/logs folder in the system file browser.")
        h.addWidget(self.open_logs_btn)
        self.open_config_btn = QPushButton("Open &config dir")
        self.open_config_btn.clicked.connect(self._open_config_dir)
        mark(
            self.open_config_btn, "Open config directory",
            "Reveal the platform config dir (state.json, history.json) in the file browser.",
        )
        h.addWidget(self.open_config_btn)
        h.addStretch(1)
        layout.addWidget(maint)

        # Save
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("&Save")
        self.save_btn.clicked.connect(self._on_save)
        mark(self.save_btn, "Save settings",
             "Persist preferences to state.json.", shortcut="Ctrl+S")
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)
        layout.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def _on_theme_change(self, theme: str) -> None:
        """Apply immediately — MainWindow listens to state.theme_changed."""
        self.state.set_theme(theme)

    def _reset_encoder_cache(self) -> None:
        from yt_uniquifier.core.encoder import CACHE_PATH
        try:
            if CACHE_PATH.exists():
                CACHE_PATH.unlink()
                QMessageBox.information(self, "Cleared", f"Removed {CACHE_PATH}")
            else:
                QMessageBox.information(self, "Cache empty", f"{CACHE_PATH} did not exist")
        except OSError as exc:
            QMessageBox.warning(self, "Reset failed", str(exc))

    def _open_log_dir(self) -> None:
        log_dir = Path.home() / ".cache" / "yt_uniquifier" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        webbrowser.open(log_dir.as_uri())

    def _open_config_dir(self) -> None:
        from yt_uniquifier.gui.state import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        webbrowser.open(CONFIG_DIR.as_uri())

    def _on_save(self) -> None:
        profile_data = self.default_profile_combo.currentData()
        if profile_data:
            self.state.set_profile_path(Path(profile_data))
        self.state.save()
        self.status_label.setText("preferences saved")
