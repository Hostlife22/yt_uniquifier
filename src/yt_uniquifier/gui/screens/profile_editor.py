"""Profile Editor — inline YAML profile editing with pydantic validation."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.profile_loader import dump_profile, load_profile
from yt_uniquifier.core.profile_marketplace import (
    Catalog,
    CatalogEntry,
    InstallResult,
    default_install_dir,
    list_entries,
)
from yt_uniquifier.core.transforms import all_ids
from yt_uniquifier.gui.a11y import mark
from yt_uniquifier.gui.paths import profiles_dir
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.marketplace_worker import (
    MarketplaceFetchWorker,
    MarketplaceInstallWorker,
)

PROFILES_DIR = profiles_dir()


class ProfileEditorScreen(ScreenBase):
    """Edit transforms (enabled toggle + params as JSON) + top-level fields.

    Per-parameter form generation per pydantic schema is a v0.6 enhancement;
    the v0.5.2 editor shows params as JSON in an editable column for
    flexibility without per-transform UI scaffolding.
    """

    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.current_profile: Profile | None = None
        self.current_path: Path | None = None
        self._build_ui()
        self._populate_profile_combo()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Profile Editor")
        title.setObjectName("title")
        layout.addWidget(title)

        # Top bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_select)
        mark(self.profile_combo, "Profile to edit",
             "Choose which YAML profile to load into the editor.")
        bar.addWidget(self.profile_combo, stretch=1)

        self.save_btn = QPushButton("&Save")
        self.save_btn.clicked.connect(self._on_save)
        mark(self.save_btn, "Save profile",
             "Overwrite the loaded YAML file with the current edits (creates .bak backup).",
             shortcut="Ctrl+S")
        bar.addWidget(self.save_btn)

        self.save_as_btn = QPushButton("Save &as…")
        self.save_as_btn.clicked.connect(self._on_save_as)
        mark(self.save_as_btn, "Save profile as",
             "Save the current edits as a new YAML file.",
             shortcut="Ctrl+Shift+S")
        bar.addWidget(self.save_as_btn)

        self.reload_btn = QPushButton("&Reload list")
        self.reload_btn.clicked.connect(self._populate_profile_combo)
        mark(self.reload_btn, "Reload profile list",
             "Rescan the profiles directory and refresh the dropdown.")
        bar.addWidget(self.reload_btn)

        # v0.9.0 R1 / F9 — community profile marketplace entry point.
        self.browse_community_btn = QPushButton("&Browse community…")
        self.browse_community_btn.clicked.connect(self._on_browse_community)
        mark(self.browse_community_btn, "Browse community profiles",
             "Fetch the community profile catalog and install one into "
             "your per-user profiles directory.")
        bar.addWidget(self.browse_community_btn)
        layout.addLayout(bar)

        # Split: transforms table | YAML preview
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Transform", "Enabled", "Params (JSON)"])
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self.table)

        right = QPlainTextEdit()
        right.setReadOnly(True)
        self.yaml_preview = right
        splitter.addWidget(right)
        splitter.setSizes([700, 400])
        layout.addWidget(splitter, stretch=1)

        # Top-level fields row
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("seed_strategy:"))
        self.seed_combo = QComboBox()
        self.seed_combo.addItems(["fixed", "per_run", "per_file", "divergent"])
        mark(self.seed_combo, "Seed strategy",
             "Controls how per-segment RNG seeds are derived "
             "(fixed reproducibility ↔ divergent uniqueness).")
        bottom.addWidget(self.seed_combo)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        # Status
        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def _populate_profile_combo(self) -> None:
        prev = self.profile_combo.currentData()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.profile_combo.addItem(p.stem, str(p))
        # Restore selection if still present.
        if prev:
            idx = self.profile_combo.findData(prev)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        # Force load of whatever is now selected.
        if self.profile_combo.count() > 0:
            self._on_profile_select(self.profile_combo.currentIndex())

    def _on_profile_select(self, _idx: int) -> None:
        path_str = self.profile_combo.currentData()
        if not path_str:
            return
        try:
            self.current_profile = load_profile(Path(path_str))
            self.current_path = Path(path_str)
        except YtUniquifierError as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        self._populate_table()
        self.seed_combo.setCurrentText(self.current_profile.seed_strategy)
        self._refresh_yaml_preview()

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        if self.current_profile is None:
            return
        known_ids = set(all_ids())
        for tc in self.current_profile.transforms:
            r = self.table.rowCount()
            self.table.insertRow(r)
            id_item = QTableWidgetItem(tc.id)
            if tc.id not in known_ids:
                id_item.setForeground(Qt.GlobalColor.red)
                id_item.setToolTip("Transform not in current registry")
            self.table.setItem(r, 0, id_item)

            enabled = QTableWidgetItem()
            enabled.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled,
            )
            enabled.setCheckState(
                Qt.CheckState.Checked if tc.enabled else Qt.CheckState.Unchecked,
            )
            self.table.setItem(r, 1, enabled)

            params = QTableWidgetItem(
                json.dumps(tc.params or {}, ensure_ascii=False),
            )
            self.table.setItem(r, 2, params)

    def _collect_profile(self) -> Profile | None:
        if self.current_profile is None:
            return None
        new_transforms: list[TransformConfig] = []
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            enabled_item = self.table.item(r, 1)
            params_item = self.table.item(r, 2)
            if id_item is None or enabled_item is None or params_item is None:
                continue
            try:
                params_dict = json.loads(params_item.text() or "{}")
            except json.JSONDecodeError as exc:
                QMessageBox.critical(
                    self, "Params JSON parse error",
                    f"Row {r + 1} ({id_item.text()}): {exc}",
                )
                return None
            new_transforms.append(TransformConfig(
                id=id_item.text(),
                enabled=enabled_item.checkState() == Qt.CheckState.Checked,
                params=params_dict,
            ))
        try:
            return self.current_profile.model_copy(update={
                "transforms": new_transforms,
                "seed_strategy": self.seed_combo.currentText(),
            })
        except Exception as exc:
            QMessageBox.critical(self, "Profile invalid", str(exc))
            return None

    def _refresh_yaml_preview(self) -> None:
        prof = self._collect_profile()
        if prof is None:
            return
        try:
            data = prof.model_dump(mode="json", exclude_none=True)
            self.yaml_preview.setPlainText(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            )
        except Exception as exc:
            self.yaml_preview.setPlainText(f"# preview error: {exc}")

    def _on_save(self) -> None:
        if self.current_path is None:
            return
        prof = self._collect_profile()
        if prof is None:
            return
        # Backup existing (best-effort).
        import contextlib
        if self.current_path.exists():
            backup = self.current_path.with_suffix(".yaml.bak")
            with contextlib.suppress(OSError):
                backup.write_bytes(self.current_path.read_bytes())
        try:
            dump_profile(prof, self.current_path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.current_profile = prof
        self._refresh_yaml_preview()
        self.status_label.setText(f"saved: {self.current_path}")

    def _on_save_as(self) -> None:
        prof = self._collect_profile()
        if prof is None:
            return
        suggested = str(PROFILES_DIR / f"{prof.name}_my.yaml")
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save profile as", suggested, "YAML (*.yaml)",
        )
        if not path_str:
            return
        try:
            dump_profile(prof, Path(path_str))
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.status_label.setText(f"saved: {path_str}")
        self._populate_profile_combo()

    # ------------------------------------------------------------------
    # v0.9.0 R1 / F9 — community marketplace
    # ------------------------------------------------------------------

    def _on_browse_community(self) -> None:
        """Open the community profile dialog and refresh on install."""
        dlg = CommunityProfilesDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.last_install is not None:
            self.status_label.setText(
                f"installed community profile: {dlg.last_install.path}",
            )
            self._populate_profile_combo()
            # Select the newly-installed profile if it appeared.
            idx = self.profile_combo.findData(str(dlg.last_install.path))
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)


class CommunityProfilesDialog(QDialog):
    """Modal that lists catalog entries and installs the selected one.

    The dialog owns two short-lived workers: one to fetch the catalog
    (re-runnable via the Refresh button) and one to install a chosen
    entry. Workers are parented to the dialog so closing it
    auto-cancels in-flight ops via ``WorkerBase.request_cancel``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Community profiles")
        self.resize(720, 420)
        self.last_install: InstallResult | None = None
        self._fetch_worker: MarketplaceFetchWorker | None = None
        self._install_worker: MarketplaceInstallWorker | None = None
        self._entries: list[CatalogEntry] = []
        self._build_ui()
        self._start_fetch(refresh=False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel(
            "Verified by SHA-256. Source: yt-uniquifier-profiles community catalog.",
        ))
        header.addStretch(1)
        self.refresh_btn = QPushButton("&Refresh")
        self.refresh_btn.clicked.connect(lambda: self._start_fetch(refresh=True))
        mark(self.refresh_btn, "Refresh catalog",
             "Re-fetch the marketplace catalog from the network.")
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["id", "name", "tags", "version"])
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        mark(self.table, "Community profile catalog",
             "Select a row and click Install to download the profile "
             "into your per-user profiles directory.")
        layout.addWidget(self.table, stretch=1)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setObjectName("detail")
        layout.addWidget(self.detail_label)

        self.status_label = QLabel("Loading catalog…")
        layout.addWidget(self.status_label)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
        )
        self.install_btn = QPushButton("&Install")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._on_install)
        mark(self.install_btn, "Install selected profile",
             "Download, verify SHA-256, and write the YAML into the "
             "per-user profiles directory.")
        buttons.addButton(
            self.install_btn,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _start_fetch(self, *, refresh: bool) -> None:
        self._cancel_workers()
        self.refresh_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.status_label.setText(
            "Refreshing catalog…" if refresh else "Loading catalog…",
        )
        worker = MarketplaceFetchWorker(refresh=refresh)
        worker.setParent(self)
        worker.catalog_ready.connect(self._on_catalog_ready)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self._fetch_worker = worker
        worker.start()

    def _on_catalog_ready(self, catalog: Catalog) -> None:
        self._entries = list_entries(catalog)
        self.table.setRowCount(0)
        for entry in self._entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(entry.id))
            self.table.setItem(r, 1, QTableWidgetItem(entry.name))
            self.table.setItem(r, 2, QTableWidgetItem(", ".join(entry.tags)))
            self.table.setItem(r, 3, QTableWidgetItem(entry.version))
        self.status_label.setText(f"{len(self._entries)} entries.")
        if self._entries:
            self.table.selectRow(0)

    def _on_fetch_failed(self, msg: str) -> None:
        self.status_label.setText(f"fetch failed: {msg}")
        QMessageBox.warning(self, "Catalog fetch failed", msg)

    def _on_selection_changed(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.install_btn.setEnabled(False)
            self.detail_label.setText("")
            return
        self.install_btn.setEnabled(True)
        self.detail_label.setText(
            f"<b>{entry.name}</b> by {entry.author}<br>"
            f"{entry.description}<br>"
            f"<code>{entry.url}</code>",
        )

    def _selected_entry(self) -> CatalogEntry | None:
        rows = self.table.selectionModel()
        if rows is None:
            return None
        indices = rows.selectedRows()
        if not indices:
            return None
        idx = indices[0].row()
        if 0 <= idx < len(self._entries):
            return self._entries[idx]
        return None

    def _on_install(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        target_path = default_install_dir() / f"{entry.id}.yaml"
        overwrite = False
        if target_path.exists():
            answer = QMessageBox.question(
                self,
                "Overwrite existing profile?",
                f"{target_path} already exists. Replace it?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True

        self.install_btn.setEnabled(False)
        self.status_label.setText(f"installing {entry.id}…")
        worker = MarketplaceInstallWorker(entry, overwrite=overwrite)
        worker.setParent(self)
        worker.installed.connect(self._on_installed)
        worker.failed.connect(self._on_install_failed)
        worker.finished.connect(lambda: self.install_btn.setEnabled(True))
        self._install_worker = worker
        worker.start()

    def _on_installed(self, result: InstallResult) -> None:
        self.last_install = result
        self.status_label.setText(
            f"installed {result.entry_id} → {result.path}",
        )
        self.accept()

    def _on_install_failed(self, msg: str) -> None:
        self.status_label.setText(f"install failed: {msg}")
        QMessageBox.critical(self, "Install failed", msg)

    def _cancel_workers(self) -> None:
        for worker in (self._fetch_worker, self._install_worker):
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                worker.quit()
                worker.wait(2000)

    def closeEvent(self, event: object) -> None:
        self._cancel_workers()
        super().closeEvent(event)  # type: ignore[arg-type]
