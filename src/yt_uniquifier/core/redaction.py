"""Bounded, recursive redaction for logs, metrics-adjacent events, and exports."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

REDACTED = "<REDACTED>"

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
_PATH_KEY = re.compile(
    r"(?:^|_)(?:path|dir|directory|file|input|output|profile)(?:$|_)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key)\s*([=:])\s*"
    r"([^\s&;,]+)"
)
_URL_CREDENTIAL = re.compile(r"(?i)([?&](?:token|key|secret|signature)=)[^&#\s]+")
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})"
)
_POSIX_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9:])(/(?:[^/\s\"'<>]+/)+[^/\s\"'<>]+)"
)
_WINDOWS_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]:\\(?:[^\\\s\"'<>]+\\)+[^\\\s\"'<>]+)"
)


def redact_path(value: str, *, all_absolute: bool = False) -> str:
    """Hide a home prefix, or every absolute path for public observability."""
    if not value:
        return value
    home = str(Path.home())
    if value == home:
        return "<HOME>"
    for separator in {os.sep, "/", "\\"}:
        prefix = home.rstrip("/\\") + separator
        if value.startswith(prefix):
            return "<HOME>" + value[len(home):]
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if all_absolute and (posix_path.is_absolute() or windows_path.is_absolute()):
        # Pure path classes deliberately avoid host-OS semantics.  Observability
        # can receive a POSIX path from a remote worker while running on Windows
        # (or a Windows/UNC path while running on POSIX), and those paths must be
        # treated as sensitive regardless of the collector's platform.
        name = windows_path.name if windows_path.is_absolute() else posix_path.name
        return f"<PATH>/{name}" if name else "<PATH>"
    return value


def redact_text(value: str, *, all_absolute_paths: bool = False) -> str:
    """Remove inline credentials and known local path prefixes from text."""
    value = _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", value)
    value = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value,
    )
    value = _URL_CREDENTIAL.sub(lambda match: match.group(1) + REDACTED, value)
    value = _KNOWN_TOKEN.sub(REDACTED, value)
    if all_absolute_paths:
        value = _POSIX_PATH_TOKEN.sub(
            lambda match: redact_path(match.group(1), all_absolute=True), value,
        )
        value = _WINDOWS_PATH_TOKEN.sub(
            lambda match: redact_path(match.group(1), all_absolute=True), value,
        )
    else:
        value = value.replace(str(Path.home()), "<HOME>")
    # Key-aware values below additionally handle single-component paths.
    return redact_path(value, all_absolute=all_absolute_paths)


def redact_mapping(
    value: Any,
    *,
    all_absolute_paths: bool = False,
    _depth: int = 0,
) -> Any:
    """Recursively redact JSON-like data with strict depth/collection bounds."""
    if _depth >= 8:
        return "<TRUNCATED>"
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 256:
                result["<truncated>"] = True
                break
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                result[key] = REDACTED
            elif isinstance(item, str) and _PATH_KEY.search(key_text):
                result[key] = redact_path(
                    redact_text(item), all_absolute=all_absolute_paths,
                )
            else:
                result[key] = redact_mapping(
                    item,
                    all_absolute_paths=all_absolute_paths,
                    _depth=_depth + 1,
                )
        return result
    if isinstance(value, (list, tuple)):
        items = [
            redact_mapping(
                item,
                all_absolute_paths=all_absolute_paths,
                _depth=_depth + 1,
            )
            for item in value[:256]
        ]
        if len(value) > 256:
            items.append("<TRUNCATED>")
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, str):
        return redact_text(value, all_absolute_paths=all_absolute_paths)
    return value


def structlog_redactor(
    _logger: object, _method_name: str, event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: no secrets or absolute path fields reach a sink."""
    redacted = redact_mapping(event_dict, all_absolute_paths=True)
    return redacted if isinstance(redacted, dict) else event_dict


__all__ = [
    "REDACTED",
    "redact_mapping",
    "redact_path",
    "redact_text",
    "structlog_redactor",
]
