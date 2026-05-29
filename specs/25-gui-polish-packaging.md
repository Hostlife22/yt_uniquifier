# Spec 25 — Settings + Corpus + Polish + Packaging (v0.5.4)

> **Phase 25 (v0.5.4)** · 2 days · **Deps:** v0.5.3

## Context

Final v0.5 release. Closes the last 2 placeholder sidebar entries
(Settings + Corpus), adds theme switcher, writes `docs/gui.md`,
attempts cross-platform packaging via PyInstaller.

After this ships: `yt-uniq-gui` имеет 100% coverage CLI команд +
validation harness + profile editor + history, плюс установщик-style
distribution для macOS / Linux / Windows (или fallback на pipx
walkthrough если PyInstaller окажется болезненным cross-platform).

## Goal

After v0.5.4:

- **Settings screen** functional: theme (dark/light/system), default
  profile, default work_dir/output_dir, default encoder, recents cap,
  history cap, "Reset encoder cache" button.
- **Corpus screen** functional: table of entries + Add/Remove/Open.
- Theme switcher applies без перезапуска (live QSS re-apply).
- `docs/gui.md` covers all 10 screens + keyboard shortcuts.
- `pyinstaller/yt-uniq-gui.spec` produces one-file `.app` (macOS) and
  `.exe` (Windows). Linux AppImage via `appimage-builder` config.
  Fallback documented: `pipx install 'yt-uniquifier[gui]'` если
  PyInstaller проваливается на одной из платформ.
- README updated с GUI screenshots + install instructions.
- Tag: `v0.5.4` (and `v0.5` final).

## Scope

**In:**
- `CorpusWorker` — wraps `Corpus.add(path, samples)` with progress signal.
- Corpus screen.
- Settings screen + theme switcher integration.
- `docs/gui.md` with screenshots.
- PyInstaller spec files (per OS).
- README rewrite with GUI section.
- ~5 new tests for Settings/Corpus.

**Not in:**
- Code signing macOS .app / Windows .exe (out of scope; users must
  unblock via Gatekeeper / SmartScreen).
- App auto-update (Sparkle / WinSparkle) — defer to v0.6.
- Localization (i18n) — English-only.
- Custom icon set per screen (use system theme icons or simple text).

## Workitem 1 — `CorpusWorker`

**File:** `src/yt_uniquifier/gui/workers/corpus_worker.py` (new)

```python
"""Index a media file into the corpus (multi-second op on long videos)."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.qa.corpus import Corpus, CorpusEntry
from yt_uniquifier.gui.workers.base import WorkerBase


class CorpusWorker(WorkerBase):
    """Add a file to the corpus. Emits CorpusEntry on success."""

    entry_added = pyqtSignal(object)            # CorpusEntry

    def __init__(self, corpus: Corpus, path: Path, *, samples: int = 60) -> None:
        super().__init__()
        self.corpus = corpus
        self.path = path
        self.samples = samples

    def run(self) -> None:
        try:
            entry = self.corpus.add(self.path, samples=self.samples)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.entry_added.emit(entry)
        self.finished_ok.emit({"entry_id": entry.id, "path": str(entry.path)})
```

**Tests:** `tests/unit/test_gui_corpus_worker.py`
- `test_corpus_worker_adds_entry` — mock `Corpus.add` → entry_added emitted
- `test_corpus_worker_failed_on_exception` — `add` raises → failed signal

## Workitem 2 — Corpus screen

**File:** `src/yt_uniquifier/gui/screens/corpus.py` (new)

```python
"""Corpus CRUD: list / add / remove."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.workers.corpus_worker import CorpusWorker


class CorpusScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.corpus = Corpus()  # default root from corpus.py constants
        self.worker: CorpusWorker | None = None
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header with corpus root
        header = QHBoxLayout()
        header.addWidget(QLabel("Corpus root:"))
        self.root_label = QLabel(str(self.corpus.root))
        self.root_label.setObjectName("path")
        header.addWidget(self.root_label, stretch=1)
        layout.addLayout(header)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Path", "Added", "Has audio FP"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Actions
        ctrl = QHBoxLayout()
        self.add_btn = QPushButton("Add file…")
        self.add_btn.clicked.connect(self._on_add)
        ctrl.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self._on_remove)
        ctrl.addWidget(self.remove_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addStretch(1)
        layout.addLayout(ctrl)

        # Status (worker progress)
        self.status = QLabel("")
        self.status.setObjectName("status")
        layout.addWidget(self.status)

    def _refresh(self) -> None:
        entries = self.corpus.list_all()
        self.table.setRowCount(0)
        for e in entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(e.id))
            self.table.setItem(r, 1, QTableWidgetItem(str(e.path)))
            self.table.setItem(r, 2, QTableWidgetItem(e.added_iso))
            self.table.setItem(r, 3, QTableWidgetItem("yes" if e.audio_fingerprint else "no"))

    def _on_add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Add to corpus", "", "Video (*.mp4 *.mov *.mkv);;All (*)"
        )
        if not path:
            return
        self.worker = CorpusWorker(self.corpus, Path(path))
        self.worker.entry_added.connect(lambda e: self._refresh())
        self.worker.failed.connect(lambda msg: self._on_failed(msg))
        self.worker.progress.connect(lambda f, m: self.status.setText(m))
        self.add_btn.setEnabled(False)
        self.worker.finished_ok.connect(lambda _: self.add_btn.setEnabled(True))
        self.worker.start()

    def _on_remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        entry_id = self.table.item(row, 0).text()
        self.corpus.remove(entry_id)
        self._refresh()

    def _on_failed(self, msg: str) -> None:
        self.add_btn.setEnabled(True)
        self.status.setText(f"FAILED: {msg}")
        QMessageBox.warning(self, "Corpus add failed", msg)
```

**Tests:** `tests/unit/test_gui_corpus_screen.py`
- `test_corpus_screen_refresh_populates_table` — mock `list_all()` returning 3 entries → table has 3 rows
- `test_corpus_screen_remove_selected` — select row 0, click Remove → `Corpus.remove` called with correct id

## Workitem 3 — Settings screen + theme switcher

**File:** `src/yt_uniquifier/gui/screens/settings.py` (new)

```python
"""Preferences. Persists to AppState which writes ~/.config/yt_uniquifier/state.json."""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from yt_uniquifier.core.encoder import _cache_path as _encoder_cache_path
from yt_uniquifier.gui.screens.base import ScreenBase
from yt_uniquifier.gui.state import AppState

PROFILES_DIR = Path(__file__).parents[2] / "profiles"


class SettingsScreen(ScreenBase):
    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Appearance
        appear = QGroupBox("Appearance")
        f = QFormLayout(appear)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light", "system"])
        self.theme_combo.setCurrentText(self.state._theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_change)
        f.addRow("Theme", self.theme_combo)
        layout.addWidget(appear)

        # Defaults
        defaults = QGroupBox("Defaults")
        f2 = QFormLayout(defaults)
        self.default_profile_combo = QComboBox()
        for p in sorted(PROFILES_DIR.glob("*.yaml")):
            self.default_profile_combo.addItem(p.stem, str(p))
        f2.addRow("Default profile", self.default_profile_combo)

        self.default_work_label = QLineEdit("")
        f2.addRow("Default work dir", self.default_work_label)
        self.default_out_label = QLineEdit("")
        f2.addRow("Default output dir", self.default_out_label)

        self.recents_cap = QSpinBox()
        self.recents_cap.setRange(5, 100)
        self.recents_cap.setValue(20)
        f2.addRow("Recents cap", self.recents_cap)

        self.history_cap = QSpinBox()
        self.history_cap.setRange(10, 1000)
        self.history_cap.setValue(100)
        f2.addRow("History cap", self.history_cap)
        layout.addWidget(defaults)

        # Maintenance
        maint = QGroupBox("Maintenance")
        h = QHBoxLayout(maint)
        self.reset_enc_btn = QPushButton("Reset encoder cache")
        self.reset_enc_btn.clicked.connect(self._reset_encoder_cache)
        h.addWidget(self.reset_enc_btn)
        self.open_logs_btn = QPushButton("Open log dir")
        self.open_logs_btn.clicked.connect(self._open_log_dir)
        h.addWidget(self.open_logs_btn)
        h.addStretch(1)
        layout.addWidget(maint)

        # Save
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        layout.addStretch(1)

    def _on_theme_change(self, theme: str) -> None:
        """Apply theme immediately (no restart)."""
        self.state.set_theme(theme)
        # MainWindow listens to state.theme_changed and re-applies qss_for().

    def _reset_encoder_cache(self) -> None:
        try:
            p = _encoder_cache_path()
            if p.exists():
                p.unlink()
            QMessageBox.information(self, "Done", f"Cache removed: {p}")
        except Exception as exc:
            QMessageBox.warning(self, "Reset failed", str(exc))

    def _open_log_dir(self) -> None:
        import webbrowser
        log_dir = Path.home() / ".cache" / "yt_uniquifier" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        webbrowser.open(log_dir.as_uri())

    def _on_save(self) -> None:
        # AppState mutations + .save() — values from form widgets.
        self.state.set_theme(self.theme_combo.currentText())
        # ... save other fields
        self.state.save()
        QMessageBox.information(self, "Saved", "Preferences saved.")
```

**File:** `src/yt_uniquifier/gui/app_pyqt.py` — connect theme switch:

```python
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        ...
        self.state.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, theme: str) -> None:
        from yt_uniquifier.gui.theme import qss_for
        self.setStyleSheet(qss_for(theme))
```

**Tests:** `tests/unit/test_gui_settings_screen.py`
- `test_theme_change_emits_signal_immediately` — change combo → `state.theme_changed` fires
- `test_save_persists_to_appstate` — populate form → save → state.save() called

## Workitem 4 — Sidebar final wiring

**File:** `src/yt_uniquifier/gui/app_pyqt.py`:

```python
from yt_uniquifier.gui.screens.corpus import CorpusScreen
from yt_uniquifier.gui.screens.settings import SettingsScreen

SIDEBAR_ITEMS = [
    ("Run",            RunScreen),
    ("Batch",          BatchScreen),
    ("Calibrate",      CalibrateScreen),
    ("QA Viewer",      QaViewerScreen),
    ("Profile Editor", ProfileEditorScreen),
    ("History",        HistoryScreen),
    ("Corpus",         CorpusScreen),
    ("Queue",          QueueScreen),
    ("Validation",     ValidationScreen),
    ("Settings",       SettingsScreen),
]
```

No more placeholders.

## Workitem 5 — `docs/gui.md`

**File:** `docs/gui.md` (new, comprehensive)

```markdown
# GUI guide

`yt-uniq-gui` is the desktop UI for yt-uniquifier. It mirrors the CLI
1:1 — anything you can do from `yt-uniq <cmd>` you can do here, plus
some extras (profile editor, history, embedded QA viewer, validation
wizard).

## Install + launch

  pip install 'yt-uniquifier[gui]'
  yt-uniq-gui

`PyQt6-WebEngine` (heavy dep ~150 MB) enables the embedded QA viewer.
If you don't install it, "Open in browser" works as a fallback.

## Screen overview

(One subsection per screen with screenshot + 50-word description.)

### Run
... (workflow, screenshot, KPI pill interpretation, "Edit profile" button)

### Batch
...

### Calibrate
...

### QA Viewer
...

### Profile Editor
...

### History
...

### Corpus
...

### Queue / Worker
...

### Validation
...

### Settings
...

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Cmd/Ctrl+1..0 | Switch to sidebar entry 1..10 |
| Cmd/Ctrl+R | Run (when on Run screen) |
| Cmd/Ctrl+. | Cancel current operation |
| Cmd/Ctrl+S | Save (Profile Editor / Settings) |
| Cmd/Ctrl+, | Open Settings |
| Cmd/Ctrl+Q | Quit |

## Where data lives

| Path | What |
|---|---|
| `~/.config/yt_uniquifier/state.json` | UI preferences, recents |
| `~/.config/yt_uniquifier/history.json` | Run history (up to 100 entries) |
| `~/.cache/yt_uniquifier/encoders.json` | Encoder detection cache (reset via Settings) |
| `~/.cache/yt_uniquifier/work/` | Default run work_dir (per plan_hash) |
| `~/.cache/yt_uniquifier/keyframes/` | Keyframe scan cache (30-day TTL) |
| `~/.cache/yt_uniquifier/corpus/` | Indexed corpus |

## Troubleshooting

(Common issues: ffmpeg not on PATH, PyQt6-WebEngine import error,
QtCharts fallback, etc.)
```

## Workitem 6 — PyInstaller spec

**File:** `pyinstaller/yt-uniq-gui.spec` (new)

```python
# PyInstaller spec for yt-uniq-gui.
# Build: pyinstaller pyinstaller/yt-uniq-gui.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Bundle shipped profiles + QA HTML template.
datas = []
datas += collect_data_files("yt_uniquifier", subdir="profiles", include_py_files=False)
datas += collect_data_files("yt_uniquifier", subdir="core/qa/templates", include_py_files=False)

# PyQt6 + WebEngine submodules.
hiddenimports = []
hiddenimports += collect_submodules("PyQt6")

a = Analysis(
    ["../src/yt_uniquifier/gui/app_pyqt.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tests"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="yt-uniq-gui",
    console=False,           # GUI app — no terminal window
    icon=None,                # add icon path here if/when available
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name="yt-uniq-gui",
)

# macOS-specific .app bundle.
import sys
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="yt-uniq-gui.app",
        icon=None,
        bundle_identifier="com.yt-uniquifier.gui",
    )
```

**Documentation in `docs/gui.md`:**

```markdown
## Packaging

### macOS .app

  pip install pyinstaller
  pyinstaller pyinstaller/yt-uniq-gui.spec --clean
  open dist/yt-uniq-gui.app

Note: app is unsigned — Gatekeeper will block. Right-click → Open to
authorize the first launch.

### Windows .exe

Same command in PowerShell. Result: `dist/yt-uniq-gui/yt-uniq-gui.exe`.
SmartScreen will warn on unsigned binary; click "More info" → "Run anyway".

### Linux AppImage

  pip install appimage-builder
  appimage-builder --recipe pyinstaller/AppImageBuilder.yml

### Fallback: pipx install

If PyInstaller fails on your platform (e.g. Wayland edge-cases):

  pipx install 'yt-uniquifier[gui]'
  yt-uniq-gui

Same UX, just no single binary.
```

## Workitem 7 — README rewrite

**File:** `README.md` — add a GUI section between "Quickstart" and
"CLI reference":

```markdown
## GUI

`yt-uniq-gui` is a desktop interface mirroring all 10 CLI commands plus
an inline profile editor, run history, and a real-CID validation
wizard. See [docs/gui.md](./docs/gui.md) for the screen-by-screen
walkthrough.

![Run screen](docs/screenshots/run.png)

  pip install 'yt-uniquifier[gui]'
  yt-uniq-gui

Pre-built bundles for macOS / Windows / Linux: see Releases.
```

Add 3–5 screenshots to `docs/screenshots/` (capture during manual smoke
test on macOS).

## Acceptance

```bash
# 1. Launch — all 10 screens functional, no placeholders.
yt-uniq-gui

# 2. Settings → switch theme to light → instant restyle, no restart.

# 3. Settings → switch back to dark → same.

# 4. Corpus → Add file → table grows → Remove → row gone.

# 5. Settings → Reset encoder cache → ~/.cache/yt_uniquifier/encoders.json removed.

# 6. PyInstaller smoke (on macOS at minimum):
pip install pyinstaller
pyinstaller pyinstaller/yt-uniq-gui.spec --clean
ls dist/yt-uniq-gui.app    # exists
open dist/yt-uniq-gui.app   # launches

# 7. docs/gui.md renders (markdown preview).

# 8. Tests + lint + types.
pytest -q tests/unit/test_gui_*
ruff check .
mypy src/yt_uniquifier/gui

# 9. Headless full-launch smoke (CI).
QT_QPA_PLATFORM=offscreen python -c "
from yt_uniquifier.gui.app_pyqt import MainWindow, QApplication
import sys
app = QApplication(sys.argv); w = MainWindow(); w.show()
# Traverse all 10 sidebar entries.
for i in range(10):
    w.sidebar.setCurrentRow(i)
    app.processEvents()
print('OK')
app.quit()
"
```

## Tests

| Уровень | Файл | Кол-во |
|---|---|---|
| Unit | `test_gui_corpus_worker.py` | 2 |
| Unit | `test_gui_corpus_screen.py` | 2 |
| Unit | `test_gui_settings_screen.py` | 2 |
| Smoke | `test_gui_full_launch.py` (updated to traverse all 10) | 1 |
| **Total** | | **~7 new** |

## Risks

| Риск | Митигация |
|---|---|
| PyInstaller fails on Windows due to PyQt6 quirks | Fallback documented: `pipx install`; if PyInstaller works on macOS+Linux, ship those bundles, ask Windows users to pipx |
| App bundle bloat (~250 MB with WebEngine) | Document size in install instructions; remind users they can skip WebEngine if they don't need embedded QA viewer |
| Theme switcher leaves stale background images | Pure QSS swap — no images involved; `setStyleSheet()` does full reapply atomically |
| Code signing not done — Gatekeeper / SmartScreen blocks | Document in install instructions; v0.6 candidate to add signing if user demand justifies |
| Settings save during a long Run — race on state.json | `AppState.save()` is synchronous and short; no realistic race |
| Corpus screen blocks during add of long video (no progress bar) | `CorpusWorker` runs in background; status label shows phase progress |
| `docs/gui.md` screenshots become stale on theme changes | Pin to dark theme for canonical screenshots; document re-capture process for future |

## Hand-off

После v0.5.4:

- **10 / 10 screens functional**. Zero placeholders.
- Theme switcher работает без перезапуска.
- `docs/gui.md` готов с screenshots.
- PyInstaller spec работает хотя бы на одной OS; documented fallback
  для остальных через pipx.
- README обновлён с GUI section.
- ~7 new tests + updated headless smoke traversing all 10 screens.
- **~80 GUI tests total** (across Specs 21–25 cumulative).

Tag: `v0.5.4` (and `v0.5` final tag pointing to same SHA).

## Effort

| Item | Time |
|---|---|
| 1. `CorpusWorker` + 2 tests | 1 hour |
| 2. Corpus screen + 2 tests | 1.5 hours |
| 3. Settings screen + 2 tests + theme switcher integration | 2 hours |
| 4. Sidebar final wiring | 15 min |
| 5. `docs/gui.md` (write + take 5 screenshots) | 2 hours |
| 6. PyInstaller spec + smoke build on macOS | 2 hours |
| 7. README rewrite + GUI section | 30 min |
| 8. Updated headless smoke test traversing 10 screens | 30 min |
| 9. Lint, type-check, commit, tag (v0.5.4 + v0.5) | 30 min |
| **Total** | **~10 hours / 2 working days** |
