"""Profile endpoints: list local + browse/install community catalog."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def register(app: Any, *, config: Any, auth: Callable[..., None]) -> None:
    from fastapi import Depends, HTTPException
    from fastapi.responses import JSONResponse

    @app.get("/api/profiles/local", dependencies=[Depends(auth)])
    def list_local() -> Any:
        roots = []
        # Bundled + per-user profile dirs are merged so the SPA shows
        # everything available to the orchestrator in one list.
        try:
            from yt_uniquifier.gui.paths import profiles_dir
            roots.append(profiles_dir())
        except Exception:  # pragma: no cover — gui extra not required for web
            pass
        if config.profile_dir is not None:
            roots.append(Path(config.profile_dir))
        seen: dict[str, dict[str, str]] = {}
        for root in roots:
            if not root.exists():
                continue
            for p in sorted(root.glob("*.yaml")):
                seen.setdefault(p.stem, {"name": p.stem, "path": str(p)})
        return JSONResponse(list(seen.values()))

    @app.get("/api/profiles/community", dependencies=[Depends(auth)])
    def list_community(refresh: bool = False) -> Any:
        from yt_uniquifier.core.profile_marketplace import (
            MarketplaceError,
            fetch_catalog,
            list_entries,
        )
        try:
            cat = fetch_catalog(refresh=refresh)
        except MarketplaceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse([e.model_dump(mode="json") for e in list_entries(cat)])

    @app.post(
        "/api/profiles/community/{entry_id}/install",
        dependencies=[Depends(auth)],
    )
    def install_community(entry_id: str) -> Any:
        from yt_uniquifier.core.profile_marketplace import (
            MarketplaceError,
            fetch_catalog,
            find_entry,
            install,
        )
        try:
            cat = fetch_catalog()
            entry = find_entry(cat, entry_id)
            result = install(entry, dest_dir=config.profile_dir, overwrite=True)
        except MarketplaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({
            "entry_id": result.entry_id,
            "path": str(result.path),
            "profile_name": result.profile_name,
        })
