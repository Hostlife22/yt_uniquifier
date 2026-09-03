"""Fail-closed ASGI request body limit tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from yt_uniquifier.web.middleware.upload_limit import ContentLengthLimitMiddleware


def _invoke(headers: list[tuple[bytes, bytes]]) -> tuple[bool, list[dict[str, Any]]]:
    called = False
    sent: list[dict[str, Any]] = []

    async def downstream(
        _scope: dict[str, Any],
        _receive: Callable[[], Awaitable[dict[str, Any]]],
        _send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = ContentLengthLimitMiddleware(downstream, max_bytes=100)
    asyncio.run(middleware(
        {"type": "http", "method": "POST", "headers": headers},
        receive,
        send,
    ))
    return called, sent


@pytest.mark.parametrize(
    ("headers", "status", "message"),
    [
        ([], 411, "required"),
        ([(b"content-length", b"invalid")], 400, "invalid"),
        ([(b"content-length", b"+1")], 400, "invalid"),
        ([(b"content-length", b" 1")], 400, "invalid"),
        ([(b"content-length", b"-1")], 400, "negative"),
        (
            [(b"content-length", b"1"), (b"content-length", b"1")],
            400,
            "Multiple",
        ),
        (
            [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
            400,
            "cannot be combined",
        ),
        ([(b"content-length", b"101")], 413, "ceiling"),
    ],
)
def test_body_length_guard_rejects_unbounded_or_invalid_requests(
    headers: list[tuple[bytes, bytes]],
    status: int,
    message: str,
) -> None:
    called, sent = _invoke(headers)

    assert called is False
    assert sent[0]["status"] == status
    body = json.loads(sent[1]["body"])
    assert message in body["detail"]


def test_valid_bounded_request_reaches_application() -> None:
    called, sent = _invoke([(b"content-length", b"100")])

    assert called is True
    assert sent == []
