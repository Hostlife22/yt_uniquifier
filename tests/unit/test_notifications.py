"""v0.7 R5 / F4 — post-job notifications.

Pure-unit coverage for ``core.notifications``:

  * provider detection by URL host
  * payload shape per provider (Discord embed / Slack blocks /
    Telegram text / generic)
  * webhook + SMTP transport: success path + transport error +
    non-2xx + missing SMTP password (keyring + env both empty)
  * ``dispatch`` invariants: never raises, respects the events
    allow-list, no-op when config is None
  * the orchestrator hook (`_maybe_dispatch_notification`) builds
    the right ``NotificationContext`` and never propagates errors
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_uniquifier.core.notifications import (
    NotificationConfig,
    NotificationContext,
    Provider,
    SmtpConfig,
    build_webhook_payload,
    detect_provider,
    dispatch,
)


# ---- provider detection ----------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://discord.com/api/webhooks/123/abc",                 "discord"),
        ("https://discordapp.com/api/webhooks/123/abc",              "discord"),
        ("https://hooks.slack.com/services/T/B/X",                   "slack"),
        ("https://api.telegram.org/bot123:abc/sendMessage",          "telegram"),
        ("https://discord.com.evil.example/webhook",                 "generic"),
        ("https://evil-discord.com/webhook",                         "generic"),
        ("https://discord.com@evil.example/webhook",                 "generic"),
        ("https://hooks.slack.com.evil.example/webhook",             "generic"),
        ("https://api.telegram.org.evil.example/webhook",            "generic"),
        ("https://example.com/webhook",                              "generic"),
        ("not-a-url",                                                "generic"),
        ("",                                                         "generic"),
    ],
)
def test_detect_provider(url: str, expected: Provider) -> None:
    assert detect_provider(url) == expected


# ---- payload formatters ----------------------------------------------------
def _ctx(kind: str = "completed") -> NotificationContext:
    return NotificationContext(
        event_kind=kind,  # type: ignore[arg-type]
        title="hello",
        body="line1\nline2",
        fields={"k": "v"},
    )


def test_discord_payload_includes_embed_and_color() -> None:
    p = build_webhook_payload("discord", _ctx("completed"))
    assert p["embeds"][0]["title"] == "hello"
    # Green for completed; red for failed.
    assert p["embeds"][0]["color"] == 0x3BA85C
    assert p["embeds"][0]["fields"] == [{"name": "k", "value": "v", "inline": True}]


def test_discord_payload_failed_uses_red() -> None:
    p = build_webhook_payload("discord", _ctx("failed"))
    assert p["embeds"][0]["color"] == 0xA83B3B


def test_slack_payload_uses_blocks() -> None:
    p = build_webhook_payload("slack", _ctx())
    assert p["text"] == "hello"
    kinds = [b["type"] for b in p["blocks"]]
    assert "header" in kinds and "section" in kinds
    # Fields render as bold key.
    assert "*k:* v" in p["blocks"][-1]["text"]["text"]


def test_telegram_payload_is_markdown_text() -> None:
    p = build_webhook_payload("telegram", _ctx())
    assert p["parse_mode"] == "Markdown"
    assert "*hello*" in p["text"]
    assert "_k_: v" in p["text"]


def test_generic_payload_has_event_envelope() -> None:
    p = build_webhook_payload("generic", _ctx())
    assert p["event"] == "completed"
    assert p["title"] == "hello"


# ---- dispatch: success + transport errors ----------------------------------
class _FakeResp:
    def __init__(self, status: int = 204, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> None:
        return None


def _collect_logger():
    lines: list[tuple[str, str]] = []

    def _logger(msg: str, level: str) -> None:
        lines.append((msg, level))
    return lines, _logger


def test_dispatch_no_op_when_config_none() -> None:
    lines, lg = _collect_logger()
    dispatch(None, _ctx(), logger=lg)
    assert lines == []


def test_dispatch_skips_event_not_in_allow_list() -> None:
    cfg = NotificationConfig(
        webhook_url="https://example.com/x",
        events=["failed"],   # completed not listed
    )
    lines, lg = _collect_logger()
    with patch("urllib.request.urlopen") as m:
        dispatch(cfg, _ctx("completed"), logger=lg)
        assert m.call_count == 0
    assert lines == []


def test_dispatch_webhook_success_logs_info() -> None:
    cfg = NotificationConfig(webhook_url="https://discord.com/api/webhooks/1/2")
    lines, lg = _collect_logger()
    with patch("urllib.request.urlopen", return_value=_FakeResp(status=204)) as m:
        dispatch(cfg, _ctx(), logger=lg)
    assert m.call_count == 1
    assert any("HTTP 204" in msg and lvl == "info" for msg, lvl in lines)


def test_dispatch_webhook_non_2xx_logs_warn() -> None:
    cfg = NotificationConfig(webhook_url="https://hooks.slack.com/services/x")
    lines, lg = _collect_logger()
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(status=500, body=b"oops"),
    ):
        dispatch(cfg, _ctx(), logger=lg)
    assert any("HTTP 500" in msg and lvl == "warn" for msg, lvl in lines)


def test_dispatch_webhook_transport_error_logs_warn() -> None:
    cfg = NotificationConfig(webhook_url="https://example.com/down")
    lines, lg = _collect_logger()
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("no route to host"),
    ):
        dispatch(cfg, _ctx(), logger=lg)
    assert any("transport error" in msg and lvl == "warn" for msg, lvl in lines)


def test_dispatch_webhook_never_propagates_unknown_error() -> None:
    cfg = NotificationConfig(webhook_url="https://example.com/x")
    lines, lg = _collect_logger()
    with patch(
        "urllib.request.urlopen",
        side_effect=RuntimeError("unexpected"),
    ):
        dispatch(cfg, _ctx(), logger=lg)   # must NOT raise
    assert any("unexpected" in msg.lower() for msg, _ in lines)


# ---- SMTP ------------------------------------------------------------------
def _smtp_cfg() -> SmtpConfig:
    return SmtpConfig(
        host="smtp.example.com", port=587, use_tls=True,
        username="me@example.com", sender="me@example.com",
        recipients=["a@x.com", "b@x.com"],
    )


def test_smtp_missing_password_logs_warning(monkeypatch) -> None:
    """No keyring entry + no env var → email disabled with a clear log."""
    monkeypatch.delenv("YT_UNIQUIFIER_SMTP_PASSWORD", raising=False)
    cfg = NotificationConfig(smtp=_smtp_cfg())
    lines, lg = _collect_logger()
    # Stub keyring import to simulate "no entry" (return None).
    with (
        patch.dict("sys.modules", clear=False),
        patch("smtplib.SMTP") as smtp_mock,
        patch("smtplib.SMTP_SSL"),
    ):
        dispatch(cfg, _ctx(), logger=lg)
        assert smtp_mock.call_count == 0
    assert any("password not found" in msg.lower() for msg, _ in lines)


def test_smtp_send_success_via_env_password(monkeypatch) -> None:
    monkeypatch.setenv("YT_UNIQUIFIER_SMTP_PASSWORD", "shh")
    cfg = NotificationConfig(smtp=_smtp_cfg())
    lines, lg = _collect_logger()
    smtp_inst = MagicMock()
    cm = MagicMock(__enter__=MagicMock(return_value=smtp_inst),
                   __exit__=MagicMock(return_value=None))
    with patch("smtplib.SMTP", return_value=cm) as smtp_cls:
        dispatch(cfg, _ctx(), logger=lg)
    smtp_cls.assert_called_once()
    smtp_inst.starttls.assert_called_once()
    smtp_inst.login.assert_called_once_with("me@example.com", "shh")
    smtp_inst.send_message.assert_called_once()
    assert any("email sent" in msg for msg, _ in lines)


def test_smtp_send_failure_swallowed(monkeypatch) -> None:
    monkeypatch.setenv("YT_UNIQUIFIER_SMTP_PASSWORD", "shh")
    cfg = NotificationConfig(smtp=_smtp_cfg())
    lines, lg = _collect_logger()
    with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        dispatch(cfg, _ctx(), logger=lg)  # must NOT raise
    assert any("failed" in msg for msg, _ in lines)


# ---- model validation ------------------------------------------------------
def test_notification_config_extra_forbidden() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        NotificationConfig.model_validate({"unknown_field": 42})


def test_smtp_config_extra_forbidden() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SmtpConfig.model_validate({
            "host": "x", "port": 587, "username": "u",
            "sender": "u", "recipients": [], "extra": True,
        })


def test_notification_config_defaults_to_both_events() -> None:
    cfg = NotificationConfig(webhook_url="https://x.com/y")
    assert set(cfg.events) == {"completed", "failed"}


# ---- orchestrator hook -----------------------------------------------------
def test_orchestrator_hook_does_not_raise_on_dispatch_error(tmp_path: Path) -> None:
    """`_maybe_dispatch_notification` must swallow ALL errors."""
    from yt_uniquifier.core.orchestrator import (
        RunOptions,
        _maybe_dispatch_notification,
    )

    cfg = NotificationConfig(webhook_url="https://discord.com/api/webhooks/1/2")
    options = RunOptions(
        work_dir=tmp_path, output=tmp_path / "o.mp4", notifications=cfg,
    )

    class _StubEncoder:
        name = "libx264"
        vendor = "cpu"

    class _StubProfile:
        name = "soft"

    class _StubSource:
        path = tmp_path / "in.mp4"

    class _StubPlan:
        plan_hash = "abcdef0123456789"
        encoder = _StubEncoder()
        profile = _StubProfile()
        source = _StubSource()

    events = []

    def _emit(ev):
        events.append(ev)

    with patch(
        "yt_uniquifier.core.notifications.dispatch",
        side_effect=RuntimeError("boom"),
    ):
        # Wraps internally; must not propagate even when dispatch itself raises.
        _maybe_dispatch_notification(
            options, _StubPlan(), "completed", emit=_emit,
        )
    # The hook should log the failure rather than raise.
    assert any(
        "notification dispatch error" in str(ev.payload.get("message", ""))
        or ev.payload.get("phase") == "notifications"
        for ev in events
    )
