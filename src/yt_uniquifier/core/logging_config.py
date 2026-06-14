"""Structured-logging configuration (v1.1.0 Task 13).

structlog is set up once per process via ``configure_logging()``. The
renderer flips between human-friendly console output (default) and
machine-parseable JSON when ``YT_UNIQ_LOG_FORMAT=json`` so the web
and Docker entry points can stream into log aggregators without
extra wiring. Log level honours ``YT_UNIQ_LOG_LEVEL`` (defaults to
``INFO``).

Every event carries an ISO-8601 UTC timestamp, the log level, the
event name, and any kwargs the caller bound — including the
``run_id`` and ``plan_hash`` that the orchestrator stamps at the
start of each run.

stdlib ``logging.getLogger(...)`` keeps working unchanged: this
module wires structlog's ``ProcessorFormatter`` into the root
stdlib handler so existing ``_log.warning("...")`` calls in
checkpoint/segmenter/preflight/etc. render through the same
renderer as the structlog bindings — no big-bang rewrite required.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

# v1.1.0 Task 13: env-var contract is part of the public surface;
# documented in README + docs/install.md. Defaults match v1.0 behaviour
# (console + INFO) so existing users see no change unless they opt in.
LOG_FORMAT_ENV = "YT_UNIQ_LOG_FORMAT"
LOG_LEVEL_ENV = "YT_UNIQ_LOG_LEVEL"
DEFAULT_LEVEL = "INFO"

_CONFIGURED = False


def _resolve_level() -> int:
    raw = os.environ.get(LOG_LEVEL_ENV, DEFAULT_LEVEL).upper()
    name_map = getattr(logging, "_nameToLevel", {})
    if raw in name_map:
        return int(logging.getLevelName(raw))
    return logging.INFO


def _is_json() -> bool:
    return os.environ.get(LOG_FORMAT_ENV, "").strip().lower() == "json"


def configure_logging(*, force: bool = False) -> None:
    """Configure structlog + the stdlib root logger.

    Idempotent: subsequent calls are no-ops unless ``force=True``.
    The CLI / GUI / web entry points all call this near startup; the
    orchestrator also calls it defensively so library consumers that
    skip the entry-point wiring still get sane defaults.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    level = _resolve_level()

    # Shared processor chain — stdlib loggers see these via
    # ProcessorFormatter; structlog loggers see them directly.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if _is_json():
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        # ConsoleRenderer is colour-by-default on TTYs but degrades to
        # plain text on non-TTY stderr — what CI logs want.
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    # Wire stdlib root → structlog renderer so legacy
    # ``logging.getLogger(__name__).warning(...)`` calls land in the
    # same format. ``foreign_pre_chain`` re-runs the shared processors
    # for stdlib events so they carry the timestamp + level keys.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        ),
    )
    root = logging.getLogger()
    # Replace handlers so a re-config under force=True doesn't stack.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Return a structlog logger bound with the given context.

    Callers in long-running flows (the orchestrator, segment workers)
    pre-bind ``run_id`` and ``plan_hash`` once so every subsequent
    event carries them without per-call boilerplate.
    """
    configure_logging()
    log = structlog.get_logger(name) if name else structlog.get_logger()
    if initial_values:
        log = log.bind(**initial_values)
    return log
