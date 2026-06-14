"""Single source of truth for GUI selections, recents, history.

Persisted under the platform's standard app-config directory
(`QStandardPaths.AppConfigLocation`):

  macOS:   ~/Library/Application Support/yt_uniquifier/
  Windows: %APPDATA%\\yt_uniquifier\\
  Linux:   $XDG_CONFIG_HOME/yt_uniquifier/  (or ~/.config/yt_uniquifier/)

A one-time migration helper copies a legacy `~/.config/yt_uniquifier/`
directory into the new location if the new location is still empty.

Screens subscribe to changes via Qt signals.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, pyqtSignal

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


def _resolve_config_dir() -> Path:
    """Return the GUI's persisted-state directory, honouring platform conventions.

    Uses `QStandardPaths.AppConfigLocation` so macOS gets `~/Library/...`,
    Windows gets `%APPDATA%\\...`, and Linux gets `$XDG_CONFIG_HOME` (or
    `~/.config` when unset). The hardcoded `~/.config/yt_uniquifier`
    path used in v0.5.x is preserved as a fallback when the QApplication
    isn't set up yet (`AppConfigLocation` returns "" without QCoreApplication).
    """
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation,
    )
    if base:
        return Path(base) / "yt_uniquifier"
    return Path.home() / ".config" / "yt_uniquifier"


def _migrate_from_legacy(new_dir: Path) -> None:
    """Best-effort one-time copy from the v0.5.x `~/.config/yt_uniquifier/` path.

    Runs only when the new directory does not exist (or has neither
    state.json nor history.json) AND the legacy path does. Uses
    `copytree` (not `move`) so the legacy directory remains as a
    backup. Failures are logged but do not block startup.
    """
    legacy = Path.home() / ".config" / "yt_uniquifier"
    if legacy == new_dir or not legacy.is_dir():
        return
    has_data = (new_dir / "state.json").exists() or (new_dir / "history.json").exists()
    if has_data:
        return
    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy, new_dir, dirs_exist_ok=True)
        _log.info("migrated GUI state %s -> %s (legacy preserved)", legacy, new_dir)
    except OSError as exc:
        _log.warning("legacy state migration failed: %s", exc)


CONFIG_DIR = _resolve_config_dir()
_migrate_from_legacy(CONFIG_DIR)
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
    notifications_changed = pyqtSignal(object)           # NotificationConfig | None
    telemetry_changed = pyqtSignal(object)               # TelemetryConfig | None
    locale_changed = pyqtSignal(str)                     # 'en_US' | 'ru_RU' | …

    def __init__(self) -> None:
        # Lazy local import to keep this module importable without the
        # core/notifications.py dependency materialised at GUI startup
        # (it pulls pydantic + urllib/smtplib paths that some headless
        # smoke tests stub).
        from yt_uniquifier.core.notifications import NotificationConfig
        from yt_uniquifier.core.telemetry import TelemetryConfig
        self._NotificationConfig = NotificationConfig
        self._TelemetryConfig = TelemetryConfig
        super().__init__()
        self._input_path: Path | None = None
        self._output_path: Path | None = None
        self._profile_path: Path | None = None
        self._encoder_name: str | None = None
        self._theme: str = "dark"
        self._recents: list[str] = []
        self._history: list[HistoryEntry] = []
        self._notifications: NotificationConfig | None = None
        self._telemetry: TelemetryConfig | None = None
        self._locale: str = "en_US"
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

    @property
    def locale(self) -> str:
        """Currently-active GUI locale (e.g. ``en_US`` or ``ru_RU``)."""
        return self._locale

    @property
    def telemetry(self) -> object:
        """Return the current ``TelemetryConfig | None``.

        Annotated as ``object`` for the same reason as
        :py:attr:`notifications` — keep AppState importable without
        materialising ``core.telemetry`` (which is harmless but
        symmetric with notifications).
        """
        return self._telemetry

    @property
    def notifications(self) -> object:
        """Return the current `NotificationConfig | None`.

        Annotated as ``object`` to keep the AppState public surface
        importable without pulling ``core.notifications`` (which pulls
        urllib/smtplib).  Callers narrow via ``isinstance`` against
        the concrete type.
        """
        return self._notifications

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

    def set_locale(self, locale: str) -> None:
        """Replace the active locale, emit signal, persist.

        Unknown locales are accepted at this layer — the i18n
        translator clamps to a known catalogue at install time so a
        stray value here at most renders English. Persisting any
        value keeps state.json round-trippable across versions even
        if a locale was added by a newer release.
        """
        if locale == self._locale:
            return
        self._locale = locale
        self.locale_changed.emit(locale)
        import contextlib
        with contextlib.suppress(OSError):
            self.save()

    def set_telemetry(self, config: object) -> None:
        """Replace the telemetry config and persist immediately.

        Accepts ``TelemetryConfig`` or ``None``; other types are
        rejected silently so an accidental ``set_telemetry(True)`` from
        a third-party screen can't poison state.json.
        """
        if config is None or isinstance(config, self._TelemetryConfig):
            self._telemetry = config
            self.telemetry_changed.emit(config)
            import contextlib
            with contextlib.suppress(OSError):
                self.save()

    def set_notifications(self, config: object) -> None:
        """Replace the notifications config and persist immediately.

        Accepts either a NotificationConfig instance or None.  Other
        types are rejected silently — callers should serialise via
        the model.  Persisted to state.json on every change so the
        Settings screen doesn't need an explicit Save click for this
        field.
        """
        if config is None or isinstance(config, self._NotificationConfig):
            self._notifications = config
            self.notifications_changed.emit(config)
            import contextlib
            with contextlib.suppress(OSError):
                self.save()

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
                loc = data.get("locale")
                if isinstance(loc, str) and loc:
                    self._locale = loc
                self._recents = list(data.get("recents", []))[:RECENTS_CAP]
                # Default selections may be absent.
                for attr, key in (
                    ("_profile_path", "profile_path"),
                    ("_encoder_name", "encoder_name"),
                ):
                    val = data.get(key)
                    if isinstance(val, str):
                        setattr(self, attr, Path(val) if attr.endswith("path") else val)
                notif = data.get("notifications")
                if isinstance(notif, dict):
                    try:
                        self._notifications = self._NotificationConfig.model_validate(
                            notif,
                        )
                    except Exception:  # noqa: BLE001 — stale schema → drop, don't crash
                        self._notifications = None
                tele = data.get("telemetry")
                if isinstance(tele, dict):
                    try:
                        self._telemetry = self._TelemetryConfig.model_validate(tele)
                    except Exception:  # noqa: BLE001 — stale schema → drop, don't crash
                        self._telemetry = None
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
        data: dict[str, object] = {
            "theme": self._theme,
            "locale": self._locale,
            "recents": list(self._recents),
            "profile_path": str(self._profile_path) if self._profile_path else None,
            "encoder_name": self._encoder_name,
        }
        if self._notifications is not None:
            # mode="json" so HttpUrl / Path-style fields serialise as strings.
            data["notifications"] = self._notifications.model_dump(mode="json")
        if self._telemetry is not None:
            data["telemetry"] = self._telemetry.model_dump(mode="json")
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
