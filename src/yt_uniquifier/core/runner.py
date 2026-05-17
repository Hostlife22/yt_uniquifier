"""Subprocess runner for ffmpeg with `-progress pipe:1` parsing and cancel."""

from __future__ import annotations

import contextlib
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand

EventKind = Literal["progress", "log", "done", "error"]


@dataclass(frozen=True)
class RunEvent:
    kind: EventKind
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    returncode: int
    duration_sec: float
    output_path: Path


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


def run(
    cmd: BuiltCommand,
    *,
    output: Path,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    log_path: Path | None = None,
    progress_via_stdout: bool = True,
) -> RunResult:
    """Execute the BuiltCommand and stream progress events.

    The command is expected to be a complete ffmpeg invocation including the
    output path. We append `-progress pipe:1 -nostats` so progress lines arrive
    on stdout while stderr carries human logs.
    """
    on_event = on_event or (lambda _e: None)

    full_cmd = list(cmd.args)
    if progress_via_stdout:
        # Insert just before the output path (last arg).
        insert_at = len(full_cmd) - 1
        full_cmd[insert_at:insert_at] = ["-progress", "pipe:1", "-nostats"]

    start = time.monotonic()
    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    log_lines: list[str] = []
    assert proc.stdout is not None

    block: dict[str, str] = {}
    try:
        for line in proc.stdout:
            if cancel_token and cancel_token.is_cancelled():
                _terminate(proc)
                break

            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            block[key] = value
            if key == "progress":
                on_event(RunEvent(kind="progress", payload=dict(block)))
                block.clear()
    finally:
        # Drain stderr without blocking too long.
        try:
            stderr_data = proc.stderr.read() if proc.stderr else ""
        except Exception:  # noqa: BLE001
            stderr_data = ""
        if stderr_data:
            log_lines.extend(stderr_data.splitlines())

    rc = proc.wait()
    duration = time.monotonic() - start

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if cancel_token and cancel_token.is_cancelled():
        on_event(RunEvent(kind="error", payload={"reason": "cancelled"}))
        raise PipelineError("cancelled by user")

    if rc != 0:
        tail = "\n".join(log_lines[-30:])
        on_event(RunEvent(kind="error", payload={"returncode": rc, "tail": tail}))
        raise PipelineError(f"ffmpeg exited with {rc}; last log:\n{tail}")

    on_event(RunEvent(kind="done", payload={"duration_sec": duration}))
    return RunResult(returncode=rc, duration_sec=duration, output_path=output)


def _terminate(proc: subprocess.Popen[str], wait_sec: float = 5.0) -> None:
    """Try SIGINT, wait, then SIGKILL."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.send_signal(signal.SIGINT)
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
