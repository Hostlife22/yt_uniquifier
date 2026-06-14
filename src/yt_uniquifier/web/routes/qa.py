"""QA report endpoints — serves `<out>.qa.html` and `<out>.qa.json`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def register(
    app: Any,
    *,
    config: Any,
    auth: Callable[..., None],
    file_response: Any,
    http_exception: Any,
) -> None:
    from fastapi import Depends

    def _safe_join(name: str, suffix: str) -> Path:
        # Defence: the SPA only ever sends the basename, but a
        # malicious client could try ``../etc/passwd``. Resolve and
        # confirm we stayed under output_dir.
        target = (config.output_dir / (name + suffix)).resolve()
        root = config.output_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise http_exception(
                status_code=400, detail="path traversal rejected",
            ) from exc
        if not target.exists():
            raise http_exception(status_code=404, detail=f"not found: {target.name}")
        return target

    @app.get("/api/qa/{name}/html", dependencies=[Depends(auth)])
    def qa_html(name: str) -> Any:
        path = _safe_join(name, ".qa.html")
        return file_response(path, media_type="text/html")

    @app.get("/api/qa/{name}/json", dependencies=[Depends(auth)])
    def qa_json(name: str) -> Any:
        path = _safe_join(name, ".qa.json")
        return file_response(path, media_type="application/json")
