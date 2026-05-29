"""Profile Editor — inline YAML profile editing with pydantic validation."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
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
)

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.profile_loader import dump_profile, load_profile
from yt_uniquifier.core.transforms import all_ids
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


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
        bar.addWidget(self.profile_combo, stretch=1)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._on_save)
        bar.addWidget(self.save_btn)

        self.save_as_btn = QPushButton("Save as…")
        self.save_as_btn.clicked.connect(self._on_save_as)
        bar.addWidget(self.save_as_btn)

        self.reload_btn = QPushButton("Reload list")
        self.reload_btn.clicked.connect(self._populate_profile_combo)
        bar.addWidget(self.reload_btn)
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
