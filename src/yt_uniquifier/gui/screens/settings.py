"""Settings — preferences with live theme switch."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.notifications_test_worker import NotificationsTestWorker

PROFILES_DIR = profiles_dir()


class SettingsScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._test_worker: NotificationsTestWorker | None = None
        self._build_ui()
        self._load_notifications_into_form()
        self._load_telemetry_into_form()
        self._refresh_telemetry_status()

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

        # v0.7 R5 / F4 — Post-job notifications.  Persists into
        # ``state.notifications`` (state.json) and is read by RunWorker
        # when constructing RunOptions for each encode.
        notif = QGroupBox("Post-job notifications (webhook + SMTP)")
        nf = QFormLayout(notif)

        self.webhook_url_edit = QLineEdit()
        self.webhook_url_edit.setPlaceholderText(
            "https://discord.com/api/webhooks/... | https://hooks.slack.com/... "
            "| https://api.telegram.org/bot.../sendMessage?chat_id=...",
        )
        mark(self.webhook_url_edit, "Webhook URL",
             "Discord / Slack / Telegram / generic JSON webhook URL.  "
             "Provider is auto-detected from the host.")
        nf.addRow("Webhook URL:", self.webhook_url_edit)

        events_row = QHBoxLayout()
        self.notify_completed_check = QCheckBox("on completion")
        self.notify_completed_check.setChecked(True)
        mark(self.notify_completed_check, "Notify on completion",
             "Send a message when a run finishes successfully.")
        events_row.addWidget(self.notify_completed_check)
        self.notify_failed_check = QCheckBox("on failure")
        self.notify_failed_check.setChecked(True)
        mark(self.notify_failed_check, "Notify on failure",
             "Send a message when a run raises an unhandled error.")
        events_row.addWidget(self.notify_failed_check)
        events_row.addStretch(1)
        nf.addRow("Send notifications:", self._row_widget(events_row))

        # SMTP (optional — left blank disables email; password comes from
        # the system keyring or YT_UNIQUIFIER_SMTP_PASSWORD env var).
        self.smtp_host_edit = QLineEdit()
        self.smtp_host_edit.setPlaceholderText("smtp.example.com (leave blank to skip SMTP)")
        mark(self.smtp_host_edit, "SMTP host",
             "SMTP server hostname.  Leave blank to disable email notifications.")
        nf.addRow("SMTP host:", self.smtp_host_edit)

        self.smtp_port_spin = QSpinBox()
        self.smtp_port_spin.setRange(1, 65535)
        self.smtp_port_spin.setValue(587)
        mark(self.smtp_port_spin, "SMTP port",
             "TCP port (typically 587 for STARTTLS or 465 for implicit TLS).")
        nf.addRow("SMTP port:", self.smtp_port_spin)

        self.smtp_use_tls_check = QCheckBox("STARTTLS (uncheck for implicit TLS / port 465)")
        self.smtp_use_tls_check.setChecked(True)
        mark(self.smtp_use_tls_check, "Use STARTTLS",
             "STARTTLS upgrade after plain connect (typical on port 587). "
             "Uncheck for implicit TLS — typical on port 465.")
        nf.addRow("Encryption:", self.smtp_use_tls_check)

        self.smtp_username_edit = QLineEdit()
        self.smtp_username_edit.setPlaceholderText("login@example.com")
        mark(self.smtp_username_edit, "SMTP username",
             "Login for the SMTP server.  Password is read from keyring "
             "or the YT_UNIQUIFIER_SMTP_PASSWORD env var; never stored here.")
        nf.addRow("SMTP username:", self.smtp_username_edit)

        self.smtp_sender_edit = QLineEdit()
        self.smtp_sender_edit.setPlaceholderText("yt-uniquifier@example.com")
        mark(self.smtp_sender_edit, "SMTP sender",
             "From: address used on outgoing notifications.")
        nf.addRow("Sender (From):", self.smtp_sender_edit)

        self.smtp_recipients_edit = QPlainTextEdit()
        self.smtp_recipients_edit.setPlaceholderText(
            "one@example.com\ntwo@example.com",
        )
        self.smtp_recipients_edit.setMaximumHeight(60)
        mark(self.smtp_recipients_edit, "SMTP recipients",
             "One email address per line.")
        nf.addRow("Recipients (one per line):", self.smtp_recipients_edit)

        # Action buttons + status line.
        notif_actions = QHBoxLayout()
        self.notif_save_btn = QPushButton("&Apply notifications")
        self.notif_save_btn.clicked.connect(self._on_notifications_apply)
        mark(self.notif_save_btn, "Apply notifications",
             "Persist the current notification config to state.json.")
        notif_actions.addWidget(self.notif_save_btn)
        self.notif_test_btn = QPushButton("Send &test notification")
        self.notif_test_btn.clicked.connect(self._on_notifications_test)
        mark(self.notif_test_btn, "Send test notification",
             "Dispatch a synthetic 'completed' notification through the configured "
             "channels so you can verify the wiring without running an encode.")
        notif_actions.addWidget(self.notif_test_btn)
        notif_actions.addStretch(1)
        nf.addRow("", self._row_widget(notif_actions))

        self.notif_status_label = QLabel("")
        self.notif_status_label.setObjectName("status")
        self.notif_status_label.setWordWrap(True)
        nf.addRow("", self.notif_status_label)

        layout.addWidget(notif)

        # v0.9 R3 — Local telemetry (opt-in, off by default, never
        # network egress). Persists into ``state.telemetry``; the
        # orchestrator reads it via RunOptions.telemetry.
        tele = QGroupBox("Local telemetry (opt-in)")
        tf = QFormLayout(tele)

        self.telemetry_enabled_check = QCheckBox(
            "Record one anonymous summary event per run",
        )
        self.telemetry_enabled_check.setChecked(False)
        mark(self.telemetry_enabled_check, "Telemetry enabled",
             "When checked, each completed or failed run appends one "
             "JSONL event to the local telemetry directory. "
             "No network egress in this release.")
        tf.addRow("Enabled:", self.telemetry_enabled_check)

        self.telemetry_redact_check = QCheckBox(
            "Strip $HOME from any path-like field",
        )
        self.telemetry_redact_check.setChecked(True)
        mark(self.telemetry_redact_check, "Redact paths",
             "Replace the current user's home prefix with <HOME> in "
             "every event before writing it.")
        tf.addRow("Redact paths:", self.telemetry_redact_check)

        self.telemetry_status_label = QLabel("")
        self.telemetry_status_label.setObjectName("status")
        self.telemetry_status_label.setWordWrap(True)
        tf.addRow("Status:", self.telemetry_status_label)

        tele_actions = QHBoxLayout()
        self.telemetry_apply_btn = QPushButton("&Apply telemetry")
        self.telemetry_apply_btn.clicked.connect(self._on_telemetry_apply)
        mark(self.telemetry_apply_btn, "Apply telemetry",
             "Persist the current telemetry config to state.json and "
             "record the consent decision.")
        tele_actions.addWidget(self.telemetry_apply_btn)
        self.telemetry_open_btn = QPushButton("Open &events folder")
        self.telemetry_open_btn.clicked.connect(self._on_telemetry_open)
        mark(self.telemetry_open_btn, "Open telemetry folder",
             "Reveal the on-disk telemetry directory in the OS file browser.")
        tele_actions.addWidget(self.telemetry_open_btn)
        self.telemetry_purge_btn = QPushButton("&Purge events")
        self.telemetry_purge_btn.clicked.connect(self._on_telemetry_purge)
        mark(self.telemetry_purge_btn, "Purge telemetry events",
             "Delete every recorded event (irreversible).")
        tele_actions.addWidget(self.telemetry_purge_btn)
        tele_actions.addStretch(1)
        tf.addRow("", self._row_widget(tele_actions))

        layout.addWidget(tele)

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

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def _row_widget(layout: QHBoxLayout) -> QWidget:
        """Wrap a QHBoxLayout in a QWidget so QFormLayout can host it."""
        w = QWidget()
        w.setLayout(layout)
        return w

    # ---- notifications --------------------------------------------------
    def _build_notification_config(self) -> object:
        """Construct a NotificationConfig from the form, or None if blank.

        Empty webhook + empty SMTP host means "silent" (returns None) so
        the user can fully disable post-job notifications without
        deleting state.json by hand.
        """
        from yt_uniquifier.core.notifications import (
            NotificationConfig,
            SmtpConfig,
        )

        webhook = self.webhook_url_edit.text().strip() or None
        smtp_host = self.smtp_host_edit.text().strip()
        smtp: SmtpConfig | None = None
        if smtp_host:
            recipients_raw = self.smtp_recipients_edit.toPlainText().strip()
            recipients = [r.strip() for r in recipients_raw.splitlines() if r.strip()]
            smtp = SmtpConfig(
                host=smtp_host,
                port=self.smtp_port_spin.value(),
                use_tls=self.smtp_use_tls_check.isChecked(),
                username=self.smtp_username_edit.text().strip(),
                sender=self.smtp_sender_edit.text().strip(),
                recipients=recipients,
            )
        if webhook is None and smtp is None:
            return None

        events: list[str] = []
        if self.notify_completed_check.isChecked():
            events.append("completed")
        if self.notify_failed_check.isChecked():
            events.append("failed")

        return NotificationConfig(
            webhook_url=webhook,
            smtp=smtp,
            events=events,  # type: ignore[arg-type]
        )

    def _load_notifications_into_form(self) -> None:
        """Mirror state.notifications into the widgets at screen build."""
        cfg = self.state.notifications
        if cfg is None:
            return
        try:
            webhook = getattr(cfg, "webhook_url", None)
            if isinstance(webhook, str):
                self.webhook_url_edit.setText(webhook)
            events = list(getattr(cfg, "events", []) or [])
            self.notify_completed_check.setChecked("completed" in events)
            self.notify_failed_check.setChecked("failed" in events)
            smtp = getattr(cfg, "smtp", None)
            if smtp is not None:
                self.smtp_host_edit.setText(str(getattr(smtp, "host", "")))
                port = getattr(smtp, "port", None)
                if isinstance(port, int):
                    self.smtp_port_spin.setValue(port)
                self.smtp_use_tls_check.setChecked(bool(getattr(smtp, "use_tls", True)))
                self.smtp_username_edit.setText(str(getattr(smtp, "username", "")))
                self.smtp_sender_edit.setText(str(getattr(smtp, "sender", "")))
                recipients = list(getattr(smtp, "recipients", []) or [])
                if recipients:
                    self.smtp_recipients_edit.setPlainText("\n".join(recipients))
        except Exception as exc:  # noqa: BLE001 — corrupt state is recoverable
            self.notif_status_label.setText(f"could not load saved config: {exc}")

    def _on_notifications_apply(self) -> None:
        try:
            cfg = self._build_notification_config()
        except Exception as exc:  # noqa: BLE001 — model validation surface
            QMessageBox.warning(self, "Invalid notification config", str(exc))
            return
        self.state.set_notifications(cfg)
        if cfg is None:
            self.notif_status_label.setText("notifications disabled (no webhook + no SMTP)")
        else:
            self.notif_status_label.setText("notifications saved.")

    def _on_notifications_test(self) -> None:
        if self._test_worker is not None:
            return
        try:
            cfg = self._build_notification_config()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Invalid notification config", str(exc))
            return
        if cfg is None:
            QMessageBox.information(
                self, "Nothing to test",
                "Configure a webhook URL or SMTP host first.",
            )
            return
        self.notif_test_btn.setEnabled(False)
        self.notif_status_label.setText("sending test…")
        worker = NotificationsTestWorker(cfg)  # type: ignore[arg-type]
        self._test_worker = worker
        worker.line.connect(self._on_notifications_test_line)
        worker.finished_ok.connect(self._on_notifications_test_finished)
        worker.failed.connect(self._on_notifications_test_failed)
        worker.start()

    def _on_notifications_test_line(self, msg: str, level: str) -> None:
        prev = self.notif_status_label.text()
        prefix = "✅" if level == "info" else "⚠️" if level == "warn" else "❌"
        self.notif_status_label.setText(f"{prev}\n{prefix} {msg}".strip())

    def _on_notifications_test_finished(self, _payload: object) -> None:
        self._drop_test_worker()

    def _on_notifications_test_failed(self, msg: str) -> None:
        self.notif_status_label.setText(f"test failed: {msg}")
        self._drop_test_worker()

    def _drop_test_worker(self) -> None:
        if self._test_worker is None:
            return
        self._test_worker.quit()
        self._test_worker.wait(2000)
        self._test_worker = None
        self.notif_test_btn.setEnabled(True)

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

    # ---- telemetry (v0.9 R3) -------------------------------------------
    def _load_telemetry_into_form(self) -> None:
        cfg = self.state.telemetry
        if cfg is None:
            return
        enabled = bool(getattr(cfg, "enabled", False))
        redact = bool(getattr(cfg, "redact_paths", True))
        self.telemetry_enabled_check.setChecked(enabled)
        self.telemetry_redact_check.setChecked(redact)

    def _refresh_telemetry_status(self) -> None:
        from yt_uniquifier.core.telemetry import (
            default_events_dir,
            event_count,
        )
        try:
            root = default_events_dir()
            count = event_count(root)
            self.telemetry_status_label.setText(
                f"{count} event(s) recorded; dir: {root}",
            )
        except Exception as exc:  # noqa: BLE001 — never crash Settings
            self.telemetry_status_label.setText(f"(status unavailable: {exc})")

    def _on_telemetry_apply(self) -> None:
        from yt_uniquifier.core.telemetry import (
            TelemetryConfig,
            write_consent_marker,
        )
        cfg = TelemetryConfig(
            enabled=self.telemetry_enabled_check.isChecked(),
            redact_paths=self.telemetry_redact_check.isChecked(),
        )
        self.state.set_telemetry(cfg)
        try:
            write_consent_marker(cfg.enabled)
        except OSError as exc:
            QMessageBox.warning(self, "Telemetry", f"could not write marker: {exc}")
        self._refresh_telemetry_status()

    def _on_telemetry_open(self) -> None:
        from yt_uniquifier.core.telemetry import default_events_dir
        root = default_events_dir()
        try:
            root.mkdir(parents=True, exist_ok=True)
            webbrowser.open(root.as_uri())
        except OSError as exc:
            QMessageBox.warning(self, "Open folder", str(exc))

    def _on_telemetry_purge(self) -> None:
        from yt_uniquifier.core.telemetry import purge_events
        answer = QMessageBox.question(
            self,
            "Purge telemetry?",
            "Delete every recorded telemetry event? This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            purge_events()
        except OSError as exc:
            QMessageBox.warning(self, "Purge failed", str(exc))
            return
        self._refresh_telemetry_status()
