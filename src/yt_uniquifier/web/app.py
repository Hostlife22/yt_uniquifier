"""FastAPI application factory + run-lifecycle store.

The factory returns a fully wired ASGI app. Tests instantiate it with
``build_app(config=…)`` against a temp work_dir; production goes
through ``yt-uniq-web`` which reads env vars + CLI flags.

Run lifecycle:
  * ``POST /api/run`` allocates a ``run_id`` (uuid4), spawns a worker
    thread that calls ``core.orchestrator.run_full``, and returns
    ``{run_id, status: "started"}``.
  * Each ``RunEvent`` is buffered into a per-run asyncio Queue.
  * ``GET /api/run/{id}/events`` upgrades to Server-Sent Events; the
    server drains the queue line-by-line until the run reports
    ``completed``/``failed``/``cancelled``.
  * ``POST /api/run/{id}/cancel`` flips a shared CancelToken; the
    next ffmpeg poll honours it (≤7 s on most encoders, see v0.5.5
    A5 daemon-watcher).

We never expose user paths verbatim outside the API — the SPA shows
basenames only, and the server-side store keeps full paths so the
client never has to round-trip them.
"""

from __future__ import annotations

import logging
import queue
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_uniquifier.core.runner import CancelToken, RunEvent

_log = logging.getLogger(__name__)


@dataclass
class WebConfig:
    """Runtime config for one FastAPI instance.

    Kept narrow — the production server reads env vars in
    ``cli.main`` and constructs this; tests pass it directly.
    """
    work_dir: Path
    output_dir: Path
    profile_dir: Path | None = None
    basic_auth_user: str | None = None
    basic_auth_pass: str | None = None
    # When set, ``/api/run`` will only accept input paths under this
    # root. Defends against an authenticated user hopping out of the
    # mounted media dir. ``None`` disables the check (useful on a
    # dev box where the LAN is trusted).
    input_root: Path | None = None


@dataclass
class _RunRecord:
    """Per-run mutable state stored in the app's in-memory registry."""

    run_id: str
    cancel_token: CancelToken
    events: queue.Queue[RunEvent | None]
    status: str = "pending"  # pending → running → completed | failed | cancelled
    error: str | None = None
    output_basename: str | None = None
    thread: threading.Thread | None = field(default=None, repr=False)


def _check_auth_required(config: WebConfig) -> bool:
    return bool(config.basic_auth_user and config.basic_auth_pass)


def build_app(config: WebConfig) -> Any:
    """Construct the FastAPI app. Lazy-imports fastapi so the rest of
    the package stays importable without the ``[web]`` extra.
    """
    try:
        from fastapi import (
            Depends,
            FastAPI,
            HTTPException,
            status,
        )
        from fastapi.responses import (
            FileResponse,
            HTMLResponse,
            JSONResponse,
            PlainTextResponse,
            StreamingResponse,
        )
        from fastapi.security import HTTPBasic, HTTPBasicCredentials
        from fastapi.staticfiles import StaticFiles
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:  # pragma: no cover — surface install hint
        raise RuntimeError(
            "FastAPI is not installed. Install: pip install 'yt-uniquifier[web]'"
        ) from exc

    runs: dict[str, _RunRecord] = {}
    runs_lock = threading.Lock()

    app = FastAPI(
        title="yt-uniquifier",
        version="0.9.0",
        docs_url="/docs",
        redoc_url=None,
    )

    # -- auth ---------------------------------------------------------
    security = HTTPBasic(auto_error=False)

    def _auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not _check_auth_required(config):
            return
        # Constant-time compare against env-supplied creds.
        u_ok = creds is not None and secrets.compare_digest(
            creds.username, config.basic_auth_user or "",
        )
        p_ok = creds is not None and secrets.compare_digest(
            creds.password, config.basic_auth_pass or "",
        )
        if not (u_ok and p_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="auth required",
                headers={"WWW-Authenticate": "Basic"},
            )

    # -- static + templates ------------------------------------------
    pkg_root = Path(__file__).resolve().parent
    jinja_env = Environment(
        loader=FileSystemLoader(str(pkg_root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    try:
        app.mount(
            "/static",
            StaticFiles(directory=str(pkg_root / "static")),
            name="static",
        )
    except RuntimeError:  # pragma: no cover — only in zipapp/PyInstaller
        _log.warning("static dir missing; SPA assets unavailable")

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(_auth)])
    def index() -> Any:
        # Rendering directly via Jinja avoids fastapi.templating's
        # required-Request injection (which trips on
        # ``from __future__ import annotations`` in this module's
        # forward references). The page is fully static; the SPA
        # fetches dynamic data via /api/* after load.
        tmpl = jinja_env.get_template("index.html")
        return HTMLResponse(tmpl.render(
            auth_required=_check_auth_required(config),
            profile_dir=str(config.profile_dir or ""),
        ))

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    # -- helpers -----------------------------------------------------
    def _validate_input(input_path: Path) -> Path:
        if not input_path.exists():
            raise HTTPException(status_code=404, detail=f"input not found: {input_path}")
        if config.input_root is not None:
            try:
                input_path.resolve().relative_to(config.input_root.resolve())
            except ValueError as exc:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"input must live under {config.input_root}; "
                        f"got {input_path}"
                    ),
                ) from exc
        return input_path

    # -- routes wired from sibling modules --------------------------
    from yt_uniquifier.web.routes.profile import register as register_profile
    from yt_uniquifier.web.routes.qa import register as register_qa
    from yt_uniquifier.web.routes.run import register as register_run

    register_run(
        app,
        config=config,
        runs=runs,
        runs_lock=runs_lock,
        auth=_auth,
        validate_input=_validate_input,
        streaming_response=StreamingResponse,
        json_response=JSONResponse,
        http_exception=HTTPException,
    )
    register_profile(app, config=config, auth=_auth)
    register_qa(
        app,
        config=config,
        auth=_auth,
        file_response=FileResponse,
        http_exception=HTTPException,
    )

    return app
