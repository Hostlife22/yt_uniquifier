"""Post-job notifications (v0.7 R5 / F4): webhook + SMTP, best-effort.

Hooked into ``orchestrator.run_full`` so a long batch on a remote box
can ping you on Discord / Slack / Telegram (or send an email) the
moment a run completes or fails.

Design constraints:

* **Never raise.** Notifications are observability, not pipeline-
  critical. Any urllib / smtplib / config error gets swallowed and
  surfaced via the optional ``logger`` callback so the GUI log shows
  what went wrong without aborting the encode the user just paid
  hours of CPU time on.
* **No new runtime deps.** ``urllib.request`` + ``smtplib`` ship with
  Python. ``keyring`` is optional (PyPI install) — missing keyring
  falls back to an env-var password, then to "SMTP disabled" rather
  than crashing.
* **Auto-detect by host.** The user pastes one webhook URL; the
  module formats the payload for whatever provider that host belongs
  to (Discord embed / Slack blocks / Telegram text / generic JSON).

The module is hermetic and side-effect free at import time: the
network calls happen only inside ``dispatch()``. Tests mock
``urllib.request.urlopen`` and ``smtplib.SMTP`` rather than
hitting real services.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)

EventKind = Literal["completed", "failed"]
Provider = Literal["discord", "slack", "telegram", "generic"]

# Conventional env var for the SMTP password fallback when `keyring`
# is unavailable. Documented in the Settings tooltip + README.
SMTP_PASSWORD_ENV = "YT_UNIQUIFIER_SMTP_PASSWORD"

# Service identifier used to scope the keyring entry so multiple
# applications can store credentials side-by-side without collision.
KEYRING_SERVICE = "yt_uniquifier_smtp"


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------

class SmtpConfig(BaseModel):
    """SMTP transport for post-job email.

    Password is intentionally NOT a field — it must come from a
    secure source (keyring or env var) so the YAML / state.json
    surface never contains it.
    """

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = Field(default=587, ge=1, le=65535)
    username: str
    sender: str
    recipients: list[str] = Field(default_factory=list)
    use_tls: bool = True


class NotificationConfig(BaseModel):
    """Top-level config attached to ``RunOptions.notifications``."""

    model_config = ConfigDict(extra="forbid")

    webhook_url: str | None = None
    smtp: SmtpConfig | None = None
    # NB: list literal needs a `cast` so mypy sees the right Literal
    # element type (it widens `["completed", "failed"]` to `list[str]`).
    events: list[EventKind] = Field(
        default_factory=lambda: cast(list[EventKind], ["completed", "failed"]),
    )
    webhook_timeout_sec: float = Field(default=5.0, gt=0.0, le=60.0)
    smtp_timeout_sec: float = Field(default=10.0, gt=0.0, le=60.0)


@dataclass(frozen=True)
class NotificationContext:
    """Bag of details `dispatch` uses to render the message.

    Decoupled from ``orchestrator.RunSummary`` so unit tests can
    construct contexts without spinning up a full Plan / Profile —
    and so renaming RunSummary fields in the future doesn't ripple
    into the notification formatters.
    """

    event_kind: EventKind
    title: str
    body: str
    # Optional extras for richer messages on Discord / Slack.
    fields: dict[str, str] | None = None


# ----------------------------------------------------------------------------
# Provider detection + payload formatters
# ----------------------------------------------------------------------------

def detect_provider(url: str) -> Provider:
    """Map a webhook URL to a payload formatter.

    Heuristic only — uses the URL host so users with a custom proxy
    that fronts Discord still work as long as the rewritten host
    matches. Unknown hosts fall through to a generic JSON envelope.
    """
    try:
        host = (urllib.parse.urlparse(url).netloc or "").lower()
    except Exception:  # noqa: BLE001 — defensive parse
        return "generic"
    if "discord.com" in host or "discordapp.com" in host:
        return "discord"
    if "hooks.slack.com" in host:
        return "slack"
    if "api.telegram.org" in host:
        return "telegram"
    return "generic"


_DISCORD_COLORS = {"completed": 0x3BA85C, "failed": 0xA83B3B}


def build_webhook_payload(
    provider: Provider,
    ctx: NotificationContext,
) -> dict[str, Any]:
    """Render the request body in whatever shape the provider expects.

    All providers receive enough info to render a useful message
    without external lookups: title + body, plus optional fields
    where the schema supports them.
    """
    fields = ctx.fields or {}
    if provider == "discord":
        embed_fields = [
            {"name": k, "value": v, "inline": True}
            for k, v in fields.items()
        ]
        return {
            "embeds": [{
                "title": ctx.title,
                "description": ctx.body,
                "color": _DISCORD_COLORS.get(ctx.event_kind, 0x808080),
                "fields": embed_fields,
            }],
        }
    if provider == "slack":
        body_lines = [ctx.body, *(f"*{k}:* {v}" for k, v in fields.items())]
        return {
            "text": ctx.title,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": ctx.title}},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": "\n".join(body_lines)}},
            ],
        }
    if provider == "telegram":
        # Telegram bot API: POST /sendMessage with chat_id in URL query
        # or body. We put it in the body to keep the URL clean; if
        # chat_id is missing from query the user's URL must include it.
        text_lines = [f"*{ctx.title}*", "", ctx.body]
        for k, v in fields.items():
            text_lines.append(f"_{k}_: {v}")
        return {"text": "\n".join(text_lines), "parse_mode": "Markdown"}
    # generic
    return {
        "event": ctx.event_kind,
        "title": ctx.title,
        "body": ctx.body,
        "fields": fields,
    }


# ----------------------------------------------------------------------------
# Transport
# ----------------------------------------------------------------------------

def _post_json(
    url: str, payload: dict[str, Any], *, timeout_sec: float,
) -> tuple[int, str]:
    """Best-effort JSON POST. Returns (status_code, body_tail).

    Callers wrap in try/except — this raises on transport / DNS /
    timeout failures, propagating just enough to log a meaningful
    warning. Body tail is capped at 200 bytes so error messages don't
    blow up the log console.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "yt-uniquifier/0.7"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310 — user-supplied URL is the point
        body = resp.read(200)
    return resp.status, body.decode("utf-8", errors="replace")


def _get_smtp_password(username: str) -> str | None:
    """Try keyring (if installed), then env var, then None.

    Order matters: keyring is the recommended path on desktop OS;
    env var covers headless / container deployments. ``None`` means
    SMTP is effectively disabled until the user configures one of
    them — dispatch surfaces that as a log warning.
    """
    # keyring is optional + can raise on a locked Wallet; suppress.
    with contextlib.suppress(Exception):
        import keyring
        pw = keyring.get_password(KEYRING_SERVICE, username)
        if pw:
            return str(pw)
    return os.environ.get(SMTP_PASSWORD_ENV)


def _send_email(
    smtp_cfg: SmtpConfig, subject: str, body: str, *, timeout_sec: float,
) -> None:
    """Send a plain-text email. Raises on transport / auth failure."""
    pw = _get_smtp_password(smtp_cfg.username)
    if not pw:
        raise RuntimeError(
            "SMTP password not found in keyring or "
            f"${SMTP_PASSWORD_ENV} env var — email notifications disabled",
        )
    msg = EmailMessage()
    msg["From"] = smtp_cfg.sender
    msg["To"] = ", ".join(smtp_cfg.recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if smtp_cfg.use_tls:
        with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=timeout_sec) as s:
            s.starttls()
            s.login(smtp_cfg.username, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, timeout=timeout_sec) as s:
            s.login(smtp_cfg.username, pw)
            s.send_message(msg)


# ----------------------------------------------------------------------------
# Public dispatch
# ----------------------------------------------------------------------------

LoggerFn = Callable[[str, str], None]   # (message, level "info"|"warn"|"error")


def dispatch(
    config: NotificationConfig | None,
    ctx: NotificationContext,
    *,
    logger: LoggerFn | None = None,
) -> None:
    """Fire all configured notification channels for one event.

    Never raises — every channel runs in its own try/except so a
    half-broken Discord URL doesn't suppress an otherwise-working
    email. Successes / failures are routed to ``logger`` so the GUI
    log shows what was sent.
    """
    if config is None:
        return
    if ctx.event_kind not in config.events:
        return

    def _log(msg: str, level: str = "info") -> None:
        if logger is not None:
            with contextlib.suppress(Exception):
                logger(msg, level)
            return
        getattr(_log, level if level in {"info", "warning", "error"}
                else "info")(msg)

    if config.webhook_url:
        provider = detect_provider(config.webhook_url)
        payload = build_webhook_payload(provider, ctx)
        try:
            status, body_tail = _post_json(
                config.webhook_url, payload,
                timeout_sec=config.webhook_timeout_sec,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log(f"webhook ({provider}) transport error: {exc}", "warn")
        except Exception as exc:  # noqa: BLE001 — never propagate
            _log(f"webhook ({provider}) unexpected error: {exc}", "warn")
        else:
            if 200 <= status < 300:
                _log(f"webhook ({provider}) sent: HTTP {status}", "info")
            else:
                _log(
                    f"webhook ({provider}) non-2xx response: "
                    f"HTTP {status} {body_tail!r}",
                    "warn",
                )

    if config.smtp and config.smtp.recipients:
        try:
            _send_email(
                config.smtp,
                subject=ctx.title,
                body=ctx.body
                + ("\n\n" + "\n".join(f"{k}: {v}"
                                       for k, v in (ctx.fields or {}).items())
                   if ctx.fields else ""),
                timeout_sec=config.smtp_timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 — never propagate
            _log(f"email to {len(config.smtp.recipients)} recipient(s) "
                 f"failed: {exc}", "warn")
        else:
            _log(f"email sent to {len(config.smtp.recipients)} recipient(s)",
                 "info")


__all__ = [
    "KEYRING_SERVICE",
    "SMTP_PASSWORD_ENV",
    "EventKind",
    "NotificationConfig",
    "NotificationContext",
    "Provider",
    "SmtpConfig",
    "build_webhook_payload",
    "detect_provider",
    "dispatch",
]
