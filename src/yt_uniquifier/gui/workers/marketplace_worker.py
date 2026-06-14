"""Background workers for the community-profile marketplace (v0.9.0 R1 / F9).

Two narrow workers mirror the corpus_worker / corpus_list_worker
split so the GUI never blocks the paint thread on a network round-trip
or a SHA-256 verification:

* ``MarketplaceFetchWorker`` — wraps ``fetch_catalog``; emits the
  parsed ``Catalog`` on success.
* ``MarketplaceInstallWorker`` — wraps ``install``; emits the
  ``InstallResult`` so the caller can refresh the per-user profile
  list and select the just-installed file.

Cancel is best-effort: the underlying ``urllib`` calls do not honour
a co-operative token, but the workers check it between fetch and
install so a cancelled "Install" never writes a file even if the
download has already started.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_marketplace import (
    DEFAULT_CATALOG_URL,
    Catalog,
    CatalogEntry,
    InstallResult,
    MarketplaceError,
    fetch_catalog,
    install,
)
from yt_uniquifier.gui.workers.base import WorkerBase


class MarketplaceFetchWorker(WorkerBase):
    """Fetch (or refresh) the marketplace catalog off the GUI thread."""

    catalog_ready = pyqtSignal(object)  # Catalog

    def __init__(
        self,
        *,
        url: str = DEFAULT_CATALOG_URL,
        refresh: bool = False,
    ) -> None:
        super().__init__()
        self.url = url
        self.refresh = refresh

    def run(self) -> None:
        try:
            catalog: Catalog = fetch_catalog(url=self.url, refresh=self.refresh)
        except (MarketplaceError, YtUniquifierError) as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if self.cancel_token.is_cancelled():
            self.failed.emit("cancelled by user")
            return
        self.catalog_ready.emit(catalog)
        self.finished_ok.emit({"entry_count": len(catalog.entries)})


class MarketplaceInstallWorker(WorkerBase):
    """Verify-and-install one catalog entry into a per-user profile dir."""

    installed = pyqtSignal(object)  # InstallResult

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        dest_dir: Path | None = None,
        overwrite: bool = False,
    ) -> None:
        super().__init__()
        self.entry = entry
        self.dest_dir = dest_dir
        self.overwrite = overwrite

    def run(self) -> None:
        if self.cancel_token.is_cancelled():
            self.failed.emit("cancelled by user")
            return
        try:
            result: InstallResult = install(
                self.entry,
                dest_dir=self.dest_dir,
                overwrite=self.overwrite,
            )
        except (MarketplaceError, YtUniquifierError) as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.installed.emit(result)
        self.finished_ok.emit({
            "entry_id": result.entry_id,
            "path": str(result.path),
            "profile_name": result.profile_name,
        })
