"""Single source of truth for GUI selections, recents, history.

Persisted to:
  ~/.config/yt_uniquifier/state.json   — current paths + prefs + recents
  ~/.config/yt_uniquifier/history.json — past runs (capped at HISTORY_CAP)

Screens subscribe to changes via Qt signals.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

_log = logging.getLogger(__name__)


def _archive_corrupt(path: Path) -> None:
    """Rename a corrupt JSON file aside so the next save doesn't overwrite it.

    Silent recovery used to hide loss of recents/history from users;
    archiving leaves a forensic trail and surfaces a clear warning.
    """
    with contextlib.suppress(OSError):
        archived = path.with_suffix(f".json.corrupt-{int(time.time())}")
        path.rename(archived)
        _log.warning("archived corrupt %s -> %s", path.name, archived.name)

CONFIG_DIR = Path.home() / ".config" / "yt_uniquifier"
STATE_PATH = CONFIG_DIR / "state.json"
HISTORY_PATH = CONFIG_DIR / "history.json"

RECENTS_CAP = 20
HISTORY_CAP = 100


@dataclass
class HistoryEntry:
    timestamp: str
    source_path: str
    profile_name: str
    encoder_name: str
    output_path: str
    qa_html_path: str | None
    plan_hash: str
    status: str             # "done" | "failed" | "cancelled"


class AppState(QObject):
    """Mutable global state with Qt signals + JSON persistence."""

    input_path_changed = pyqtSignal(object)              # Path | None
    output_path_changed = pyqtSignal(object)
    profile_path_changed = pyqtSignal(object)
    encoder_name_changed = pyqtSignal(object)            # str | None ('auto' → None)
    theme_changed = pyqtSignal(str)                      # 'dark' | 'light' | 'system'
    recents_changed = pyqtSignal(list)                   # list[str]
    history_changed = pyqtSignal(list)                   # list[HistoryEntry]

    def __init__(self) -> None:
        super().__init__()
        self._input_path: Path | None = None
        self._output_path: Path | None = None
        self._profile_path: Path | None = None
        self._encoder_name: str | None = None
        self._theme: str = "dark"
        self._recents: list[str] = []
        self._history: list[HistoryEntry] = []
        self._load()

    # ---- read-only accessors (test-friendly) ----
    @property
    def input_path(self) -> Path | None:
        return self._input_path

    @property
    def output_path(self) -> Path | None:
        return self._output_path

    @property
    def profile_path(self) -> Path | None:
        return self._profile_path

    @property
    def encoder_name(self) -> str | None:
        return self._encoder_name

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def recents(self) -> list[str]:
        return list(self._recents)

    @property
    def history(self) -> list[HistoryEntry]:
        return list(self._history)

    # ---- setters with signal emission ----
    def set_input_path(self, path: Path | None) -> None:
        self._input_path = path
        self.input_path_changed.emit(path)
        if path is not None:
            self.push_recent(str(path))

    def set_output_path(self, path: Path | None) -> None:
        self._output_path = path
        self.output_path_changed.emit(path)

    def set_profile_path(self, path: Path | None) -> None:
        self._profile_path = path
        self.profile_path_changed.emit(path)

    def set_encoder_name(self, name: str | None) -> None:
        self._encoder_name = name
        self.encoder_name_changed.emit(name)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.theme_changed.emit(theme)

    # ---- recents ----
    def push_recent(self, path: str) -> None:
        """Add to front, dedup, cap at RECENTS_CAP. Persist immediately.

        Recents was previously only flushed when the user visited
        Settings and clicked Save — closing without that step lost
        the list. Persist on every push so the next session sees the
        same recents even after a crash.
        """
        if path in self._recents:
            self._recents.remove(path)
        self._recents.insert(0, path)
        self._recents = self._recents[:RECENTS_CAP]
        self.recents_changed.emit(list(self._recents))
        import contextlib
        with contextlib.suppress(OSError):
            self.save()

    # ---- history ----
    def push_history(self, entry: HistoryEntry) -> None:
        """Prepend entry, cap at HISTORY_CAP, persist to disk."""
        self._history.insert(0, entry)
        self._history = self._history[:HISTORY_CAP]
        self._save_history()
        self.history_changed.emit(list(self._history))

    def clear_history(self) -> None:
        self._history = []
        self._save_history()
        self.history_changed.emit([])

    # ---- persistence ----
    def _load(self) -> None:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                self._theme = data.get("theme", "dark")
                self._recents = list(data.get("recents", []))[:RECENTS_CAP]
                # Default selections may be absent.
                for attr, key in (
                    ("_profile_path", "profile_path"),
                    ("_encoder_name", "encoder_name"),
                ):
                    val = data.get(key)
                    if isinstance(val, str):
                        setattr(self, attr, Path(val) if attr.endswith("path") else val)
        except json.JSONDecodeError:
            # File exists but isn't parseable — archive it so we don't
            # silently lose the user's recents/prefs on the next save.
            _archive_corrupt(STATE_PATH)
        except OSError as exc:
            _log.warning("could not read %s: %s", STATE_PATH, exc)

        try:
            if HISTORY_PATH.exists():
                raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                self._history = [
                    HistoryEntry(**e) for e in raw[:HISTORY_CAP]
                    if isinstance(e, dict)
                ]
        except json.JSONDecodeError:
            _archive_corrupt(HISTORY_PATH)
            self._history = []
        except (OSError, TypeError) as exc:
            _log.warning("could not read %s: %s", HISTORY_PATH, exc)
            self._history = []

    def save(self) -> None:
        """Persist non-history state to STATE_PATH."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "theme": self._theme,
            "recents": list(self._recents),
            "profile_path": str(self._profile_path) if self._profile_path else None,
            "encoder_name": self._encoder_name,
        }
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)

    def _save_history(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps([asdict(e) for e in self._history], indent=2),
            encoding="utf-8",
        )
        tmp.replace(HISTORY_PATH)
