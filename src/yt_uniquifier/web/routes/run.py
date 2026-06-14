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
from typing import Any

from pydantic import BaseModel, Field

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import CancelToken, RunEvent

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


def register(  # noqa: PLR0913
    app: Any,
    *,
    config: Any,
    runs: dict[str, Any],
    runs_lock: threading.Lock,
    auth: Callable[..., None],
    validate_input: Callable[[Path], Path],
    streaming_response: Any,
    json_response: Any,
    http_exception: Any,
) -> None:
    from fastapi import Depends

    @app.post("/api/run", dependencies=[Depends(auth)])
    def start_run(req: RunRequest) -> Any:
        # Resolve paths through the validator so a bad input is a 404
        # before we touch the orchestrator.
        input_path = validate_input(Path(req.input_path).expanduser())
        profile_path = Path(req.profile_path).expanduser()
        if not profile_path.exists():
            raise http_exception(
                status_code=404, detail=f"profile not found: {profile_path}",
            )
        try:
            profile = load_profile(profile_path)
        except Exception as exc:  # noqa: BLE001
            raise http_exception(
                status_code=400, detail=f"profile load failed: {exc}",
            ) from exc

        out_name = req.output_name or f"{input_path.stem}__{profile.name}.mp4"
        output_path = config.output_dir / out_name
        config.output_dir.mkdir(parents=True, exist_ok=True)

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
    def cancel_run(run_id: str) -> Any:
        record = runs.get(run_id)
        if record is None:
            raise http_exception(status_code=404, detail="unknown run_id")
        record.cancel_token.cancel()
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
