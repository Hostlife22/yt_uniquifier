"""POST /api/run + SSE event stream + cancel endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import CancelToken, RunEvent

# Module-level import so ``from __future__ import annotations`` doesn't
# hide ``Request`` from FastAPI's ``typing.get_type_hints`` lookup —
# without this, ``request: Request`` becomes a 422 query parameter
# binding because FastAPI can't resolve the string to a class.
if TYPE_CHECKING:
    from fastapi import Request  # noqa: F401
else:
    try:
        from fastapi import Request
    except ImportError:  # pragma: no cover — [web] extra missing
        Request = Any  # type: ignore[assignment,misc]

_log = logging.getLogger(__name__)


class RunRequest(BaseModel):
    input_path: str = Field(min_length=1)
    profile_path: str = Field(min_length=1)
    output_name: str | None = Field(default=None, max_length=128)
    encoder_override: str | None = None
    workers: int = Field(default=1, ge=1, le=64)


_TERMINAL_KINDS = {"completed", "failed", "cancelled", "error"}


def _drain_to_jsonl(event: RunEvent) -> str:
    return json.dumps({"kind": event.kind, "payload": event.payload}, default=str)


def _principal_from_request(request: Any) -> str | None:
    """Decode the basic-auth principal for audit logging.

    Best-effort: returns None on a malformed or absent header. We do
    NOT verify the credentials here — auth has already run via the
    Depends(auth) dependency. The principal is only used as a log
    correlation key.
    """
    import base64
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded.split(":", 1)[0] or None


def register(  # noqa: PLR0913
    app: Any,
    *,
    config: Any,
    runs: dict[str, Any],
    runs_lock: threading.Lock,
    auth: Callable[..., None],
    validate_input: Callable[[Path], Path],
    validate_profile: Callable[[Path], Path],
    streaming_response: Any,
    json_response: Any,
    http_exception: Any,
    limiter: Any = None,
) -> None:
    from fastapi import Depends

    # v1.1.0 Task 16: opt-in rate limit decorator. When ``limiter`` is
    # None (e.g. slowapi not installed, or LAN-trusted deployment with
    # config.rate_limit_run=None) we fall back to a no-op pass-through.
    if limiter is not None and getattr(config, "rate_limit_run", None):
        run_rate_limit = limiter.limit(config.rate_limit_run)
    else:
        def run_rate_limit(fn: Any) -> Any:
            return fn

    @app.post("/api/run", dependencies=[Depends(auth)])
    @run_rate_limit
    def start_run(request: Request, req: RunRequest) -> Any:
        # Resolve paths through the validator so a bad input is a 404
        # before we touch the orchestrator.
        input_path = validate_input(Path(req.input_path).expanduser())
        profile_path = validate_profile(Path(req.profile_path).expanduser())
        try:
            profile = load_profile(profile_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning("profile load failed", exc_info=exc)
            raise http_exception(
                status_code=400, detail="profile is invalid",
            ) from exc

        out_name = req.output_name or f"{input_path.stem}__{profile.name}.mp4"
        if (
            out_name in {"", ".", ".."}
            or "/" in out_name
            or "\\" in out_name
            or Path(out_name).is_absolute()
            or Path(out_name).name != out_name
        ):
            raise http_exception(status_code=400, detail="invalid output_name")
        output_root = Path(config.output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        output_path = (output_root / out_name).resolve()
        try:
            output_path.relative_to(output_root)
        except ValueError as exc:
            raise http_exception(status_code=400, detail="invalid output_name") from exc

        try:
            plan = build_plan(input_path, profile, req.encoder_override)
        except Exception as exc:  # noqa: BLE001
            raise http_exception(
                status_code=400, detail=f"plan build failed: {exc}",
            ) from exc

        run_id = uuid.uuid4().hex
        cancel_token = CancelToken()
        events_q: queue.Queue[RunEvent | None] = queue.Queue(maxsize=10_000)

        from yt_uniquifier.web.app import _RunRecord
        record = _RunRecord(
            run_id=run_id,
            cancel_token=cancel_token,
            events=events_q,
            status="pending",
            output_basename=output_path.name,
        )

        def _on_event(ev: RunEvent) -> None:
            # v1.1.0 Task 15: feed the same RunEvent stream into the
            # Prometheus counters before the SSE queue so the queue's
            # back-pressure-drop path doesn't lose metric updates.
            try:
                from yt_uniquifier.web.metrics import update_from_event
                update_from_event(ev)
            except Exception:  # pragma: no cover — never block runs
                pass
            # Best-effort enqueue; if the SSE consumer disappeared,
            # the queue may fill — drop oldest by pulling one item.
            import contextlib
            try:
                events_q.put_nowait(ev)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    events_q.get_nowait()
                with contextlib.suppress(queue.Full):
                    events_q.put_nowait(ev)

        def _runner() -> None:
            record.status = "running"
            try:
                opts = RunOptions(
                    work_dir=config.work_dir / run_id,
                    output=output_path,
                    encoder_override=req.encoder_override,
                    workers=req.workers,
                    # v1.1.0 Task 14: hand the HTTP-layer correlation ID
                    # to the orchestrator so the structured log emitted
                    # from run_full carries the same `run_id` that's in
                    # the HTTP response body and the SSE event stream.
                    run_id=run_id,
                )
                run_full(plan, opts, on_event=_on_event, cancel_token=cancel_token)
                record.status = (
                    "cancelled" if cancel_token.is_cancelled() else "completed"
                )
            except BaseException as exc:  # noqa: BLE001 — capture everything
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                _on_event(RunEvent(kind="error", payload={
                    "phase": "runner", "message": record.error,
                }))
            finally:
                events_q.put(None)  # sentinel ends SSE stream

        thread = threading.Thread(
            target=_runner, name=f"run-{run_id}", daemon=True,
        )
        record.thread = thread
        with runs_lock:
            runs[run_id] = record
        thread.start()
        # v1.1.0 Task 16: audit only AFTER the run is actually queued
        # so we don't record requests that bounced earlier in the
        # validation chain (they're surfaced as 4xx, not as audit
        # events). Principal is extracted from the basic-auth header
        # if present.
        from yt_uniquifier.web.audit import audit
        audit(
            "api.run.start",
            principal=_principal_from_request(request),
            payload={
                "run_id": run_id,
                "input": str(input_path),
                "profile": str(profile_path),
                "output_basename": output_path.name,
            },
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        return json_response({
            "run_id": run_id,
            "output_basename": output_path.name,
            "status": record.status,
        })

    @app.get("/api/run/{run_id}/status", dependencies=[Depends(auth)])
    def run_status(run_id: str) -> Any:
        record = runs.get(run_id)
        if record is None:
            raise http_exception(status_code=404, detail="unknown run_id")
        return json_response({
            "run_id": run_id,
            "status": record.status,
            "error": record.error,
            "output_basename": record.output_basename,
        })

    @app.post("/api/run/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel_run(run_id: str, request: Request) -> Any:
        record = runs.get(run_id)
        if record is None:
            raise http_exception(status_code=404, detail="unknown run_id")
        record.cancel_token.cancel()
        from yt_uniquifier.web.audit import audit
        audit(
            "api.run.cancel",
            principal=_principal_from_request(request),
            payload={"run_id": run_id},
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        return json_response({"run_id": run_id, "cancel_requested": True})

    @app.get("/api/run/{run_id}/events", dependencies=[Depends(auth)])
    async def stream_events(run_id: str) -> Any:
        record = runs.get(run_id)
        if record is None:
            raise http_exception(status_code=404, detail="unknown run_id")

        async def _generator() -> AsyncIterator[bytes]:
            loop = asyncio.get_running_loop()
            while True:
                # `queue.get` is blocking; pop it on a worker thread so
                # the event loop stays responsive to disconnects.
                ev = await loop.run_in_executor(None, record.events.get)
                if ev is None:
                    break
                payload = _drain_to_jsonl(ev)
                yield f"data: {payload}\n\n".encode()
                if ev.kind in _TERMINAL_KINDS:
                    break
            # Always send a terminal marker so the client knows to
            # close the EventSource cleanly even on cancel.
            final = json.dumps({"status": record.status, "error": record.error})
            yield f"event: end\ndata: {final}\n\n".encode()

        return streaming_response(
            _generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx: disable buffering
            },
        )
