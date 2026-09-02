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
import os
import queue
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_uniquifier import __version__
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
    # ``/api/run`` only accepts input paths under this root. ``None``
    # selects the process working directory, keeping the default secure
    # while allowing deployments to opt into another mounted media dir.
    input_root: Path | None = None
    # v1.1.0 Task 16: state-changing requests are rate-limited per
    # principal (basic-auth user) or per client IP. Defaults match
    # the plan's matrix (30/min for /api/run; 5/min for any future
    # /api/upload). Set ``None`` on a trusted LAN to disable.
    rate_limit_run: str | None = "30/minute"
    rate_limit_upload: str | None = "5/minute"
    # v1.1.0 Task 16: hard ceiling on incoming Content-Length so a
    # rogue client can't make Starlette buffer multi-GB POST bodies.
    # 5 GiB matches the plan; legitimate uploads of larger sources
    # should land in ``input_root`` via SCP/NFS instead.
    max_upload_bytes: int = 5 * 1024 * 1024 * 1024
    # v1.1.0 Task 16: append-only JSONL audit log of state-changing
    # requests. ``None`` = disabled (default keeps unit tests hermetic).
    audit_log_path: Path | None = None
    # Hard process-level backpressure. Each run can itself spawn many FFmpeg
    # workers, so accepting an unbounded number of run threads is unsafe.
    max_concurrent_runs: int = 2


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

    # v1.1.0 Task 15: register the Prometheus counter families at
    # build_app time so the very first /metrics scrape includes them
    # even before any run has fired an event.
    from yt_uniquifier.web import metrics as _metrics  # noqa: F401

    app = FastAPI(
        title="yt-uniquifier",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )

    # v1.1.0 Task 16: hard upload ceiling first so any later route
    # additions inherit the cap automatically.
    from yt_uniquifier.web.middleware.upload_limit import (
        ContentLengthLimitMiddleware,
    )
    app.add_middleware(
        ContentLengthLimitMiddleware,
        max_bytes=config.max_upload_bytes,
    )

    # v1.1.0 Task 16: slowapi rate limiter keyed on principal+IP.
    # Each route opts in via the limiter's decorator (wired in
    # routes/run.py.register). The 429 handler returns JSON so SPA
    # clients get a parseable error.
    try:
        from slowapi import Limiter
        from slowapi.errors import RateLimitExceeded
        from slowapi.util import get_remote_address

        def _key(request: Any) -> str:
            # Prefer the basic-auth principal so two users behind
            # the same NAT each get their own bucket; fall back to
            # the client IP when auth is disabled.
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("basic "):
                # Bucket by the encoded principal — we don't need to
                # decode it, identical strings map to the same bucket.
                return f"basic:{auth.split(' ', 1)[1][:32]}"
            return f"ip:{get_remote_address(request)}"

        limiter = Limiter(key_func=_key, default_limits=[])
        app.state.limiter = limiter

        async def _ratelimit_handler(
            request: Any, exc: Exception,
        ) -> Any:
            # exc is RateLimitExceeded in practice — Starlette's
            # signature is Exception-typed though, so we accept the
            # broader hint and downcast at the use site.
            detail = getattr(exc, "detail", str(exc))
            return JSONResponse(
                {"detail": f"rate limit exceeded: {detail}"},
                status_code=429,
            )

        app.add_exception_handler(RateLimitExceeded, _ratelimit_handler)
    except ImportError:  # pragma: no cover — [web] extra missing slowapi
        limiter = None

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

    @app.get("/readyz")
    def readyz() -> Any:
        """v1.1.0 Task 15: readiness probe.

        Distinct from /healthz — readyz returns 503 when the server is
        running but can't currently accept work (encoder cache empty,
        work_dir read-only, etc.). Liveness probes use /healthz,
        traffic gates use /readyz.
        """
        checks: dict[str, str] = {}
        ready = True

        # Cheap: pull from encoder cache (≤200 ms typical) so a cold
        # detection doesn't make /readyz spawn 10 ffmpeg probes per
        # health-check tick.
        try:
            from yt_uniquifier.core.encoder import detect_encoders
            working = [c for c in detect_encoders() if c.works]
            if not working:
                ready = False
                checks["encoders"] = "no working encoder detected"
            else:
                checks["encoders"] = f"{len(working)} working"
        except Exception as exc:  # noqa: BLE001
            ready = False
            checks["encoders"] = f"probe failed: {type(exc).__name__}"

        # work_dir must be writable for state.json / segment files.
        try:
            config.work_dir.mkdir(parents=True, exist_ok=True)
            probe = config.work_dir / ".readyz_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks["work_dir"] = "writable"
        except OSError as exc:
            ready = False
            # The response is unauthenticated and may be Internet-facing.
            # Keep filesystem paths and OS error details in server logs only.
            _log.warning("readiness work_dir probe failed: %s", exc)
            checks["work_dir"] = "unwritable"

        payload: dict[str, Any] = {"ready": ready, "checks": checks}
        return JSONResponse(
            payload,
            status_code=200 if ready else 503,
        )

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        """v1.1.0 Task 15: Prometheus text-format metrics.

        Scraper-facing endpoint — no auth so cluster-internal Prometheus
        servers don't need basic-auth wiring. Operators who expose
        /metrics publicly should put it behind a network ACL.
        """
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            generate_latest,
        )
        return PlainTextResponse(
            generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # -- helpers -----------------------------------------------------
    # Freeze the default boundary at app construction time. A library or
    # plugin changing the process cwd later must not move the security root.
    configured_input_root = config.input_root or Path.cwd()
    input_root_text = os.path.realpath(os.fspath(configured_input_root))
    input_root_prefix = input_root_text.rstrip(os.sep) + os.sep

    def _validate_input(raw_path: str) -> Path:
        """Normalize an input and confine it to the configured media root."""
        resolved_text = os.path.realpath(os.path.expanduser(raw_path))
        if (
            resolved_text != input_root_text
            and not resolved_text.startswith(input_root_prefix)
        ):
            raise HTTPException(status_code=403, detail="input is outside allowed root")

        # Do not turn request data into a Path until it has passed the
        # realpath + trusted-prefix boundary above.
        input_path = Path(resolved_text)
        if not input_path.is_file():
            raise HTTPException(status_code=404, detail="input not found")
        return input_path

    def _validate_profile(raw_path: str) -> Path:
        """Resolve a profile only when it belongs to an advertised root."""
        resolved_text = os.path.realpath(os.path.expanduser(raw_path))

        roots: list[Path] = []
        try:
            from yt_uniquifier.gui.paths import profiles_dir

            roots.append(profiles_dir())
        except Exception:  # pragma: no cover — gui extra not required for web
            pass
        if config.profile_dir is not None:
            roots.append(Path(config.profile_dir))

        for root in roots:
            root_text = os.path.realpath(os.fspath(root))
            root_prefix = root_text.rstrip(os.sep) + os.sep
            if resolved_text != root_text and not resolved_text.startswith(root_prefix):
                continue

            # As with input paths, filesystem access only happens after the
            # normalized candidate has been confined to a trusted root.
            resolved = Path(resolved_text)
            if not resolved.is_file():
                raise HTTPException(status_code=404, detail="profile not found")
            return resolved
        raise HTTPException(status_code=403, detail="profile is outside allowed roots")

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
        validate_profile=_validate_profile,
        streaming_response=StreamingResponse,
        json_response=JSONResponse,
        http_exception=HTTPException,
        limiter=limiter,
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
