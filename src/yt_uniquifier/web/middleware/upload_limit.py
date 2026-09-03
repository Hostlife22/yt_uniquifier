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

        # Pull Content-Length header. Starlette stores headers as a list of
        # (bytes, bytes) tuples (lowercase name). A body-bearing request without
        # a valid non-negative length is rejected: delegating chunked/malformed
        # bodies would let callers bypass the configured hard ceiling.
        raw_headers = list(scope.get("headers") or [])
        length_values = [
            value for name, value in raw_headers if name.lower() == b"content-length"
        ]
        if len(length_values) > 1:
            await self._reject(
                send, status=400, detail="Multiple Content-Length headers are not allowed.",
            )
            return
        if not length_values:
            await self._reject(
                send,
                status=411,
                detail="Content-Length is required for body-bearing requests.",
            )
            return
        if any(name.lower() == b"transfer-encoding" for name, _value in raw_headers):
            await self._reject(
                send,
                status=400,
                detail="Transfer-Encoding and Content-Length cannot be combined.",
            )
            return
        cl_raw = length_values[0]
        if cl_raw.startswith(b"-"):
            await self._reject(send, status=400, detail="Content-Length cannot be negative.")
            return
        if not cl_raw.isdigit():
            await self._reject(send, status=400, detail="Content-Length is invalid.")
            return
        cl = int(cl_raw)
        if cl > self.max_bytes:
            await self._reject(
                send,
                status=413,
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
        status: int,
        detail: str,
    ) -> None:
        import json
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
