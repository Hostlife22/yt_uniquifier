"""Reject requests whose Content-Length exceeds the configured ceiling.

v1.1.0 Task 16. Streaming-friendly: we inspect the header and short-
circuit with 413 BEFORE Starlette reads the body, so a malicious
client cannot make us buffer multi-GB payloads only to discard them.

Mounted as an ASGI middleware so it applies to every route in one
shot — even a future ``/api/upload`` endpoint will inherit the cap
without re-wiring.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class ContentLengthLimitMiddleware:
    """ASGI middleware enforcing a per-request Content-Length ceiling.

    Args:
        app: the wrapped ASGI app.
        max_bytes: refuse any request whose ``Content-Length`` header
            declares a value above this number. Requests without the
            header (chunked uploads) are also refused — chunked
            requests are uncommon outside intentional uploads and
            allowing them would defeat the cap.
    """

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError(
                f"max_bytes must be > 0; got {max_bytes}",
            )
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        # Apply only to HTTP requests with a body-bearing method.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = (scope.get("method") or "").upper()
        if method not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        # Pull Content-Length header. Starlette stores headers as
        # a list of (bytes, bytes) tuples (lowercase name). Absent or
        # malformed Content-Length is delegated to Starlette — the
        # cap only fires on values clearly above the ceiling.
        headers = dict(scope.get("headers") or [])
        cl_raw = headers.get(b"content-length")
        if cl_raw is None:
            await self.app(scope, receive, send)
            return
        try:
            cl = int(cl_raw)
        except ValueError:
            await self.app(scope, receive, send)
            return
        if cl > self.max_bytes:
            await self._reject(
                send,
                detail=(
                    f"Request body is {cl} bytes; the ceiling is "
                    f"{self.max_bytes} bytes."
                ),
            )
            return
        await self.app(scope, receive, send)

    async def _reject(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        detail: str,
    ) -> None:
        import json
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
