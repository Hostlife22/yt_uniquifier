"""Cross-OS process suspend/resume primitives used by F5 pause/resume.

Public API:

* ``suspend_process_tree(pid)`` — best-effort SIGSTOP (POSIX) or
  ``psutil.Process.suspend()`` (Windows) over the process + every
  reachable descendant.
* ``resume_process_tree(pid)`` — counterpart SIGCONT / ``resume()``.

Both functions are **best-effort** and **never raise**: a failed
syscall is logged at WARN and reported via the return value, but the
caller (Runner / orchestrator pause path) must keep going regardless —
a partial pause on a misbehaving child must not corrupt orchestrator
state, and a partial resume must not strand a single ffmpeg subprocess
forever.  Return is the count of processes that ack'd the operation,
which lets tests assert "at least the root was hit".

Design notes:

* ``ffmpeg`` itself rarely spawns child processes for normal encodes;
  the recursive walk is mostly a Windows-side defensive measure (where
  ``psutil.Process.suspend`` does **not** propagate to children by
  default) but it is also useful on POSIX for hardware-encoder paths
  that fork helper processes.
* ``psutil`` is a soft dependency: POSIX falls back to stdlib
  ``os.kill`` + ``/proc`` walking if psutil is absent.  Windows
  requires psutil — without it, ``suspend_process_tree`` returns 0
  and logs a clear error, but the caller still completes (pause
  becomes a no-op rather than crashing the run).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Iterable

_log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")


def _try_import_psutil() -> object | None:
    """Lazy psutil import — returns None if the package isn't installed.

    Kept as a function (not module-level) so the import isn't paid on
    every yt-uniquifier startup. The orchestrator only touches this
    module when a PauseToken is wired up.
    """
    try:
        import psutil
    except ImportError:
        return None
    return psutil  # type: ignore[no-any-return]


def _iter_descendants_psutil(pid: int, psutil: object) -> Iterable[int]:
    """Yield every descendant PID via psutil's process tree walk."""
    try:
        proc = psutil.Process(pid)  # type: ignore[attr-defined]
        for child in proc.children(recursive=True):
            yield int(child.pid)
    except Exception as exc:  # noqa: BLE001 — best-effort enumeration
        _log.warning(
            "process_control: psutil descendant walk for pid=%s failed: %s",
            pid, exc,
        )


def _iter_descendants_proc(pid: int) -> Iterable[int]:
    """POSIX fallback descendant walk via ``/proc/<pid>/task/.../children``.

    No psutil, no fork. Returns silently on Darwin (no /proc), where
    descendants are skipped — the root SIGSTOP still lands, which is
    what matters for ffmpeg's single-process encode case.
    """
    if not os.path.isdir("/proc"):
        return
    stack = [pid]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current != pid:
            yield current
        try:
            task_dir = f"/proc/{current}/task"
            for tid in os.listdir(task_dir):
                children_path = f"{task_dir}/{tid}/children"
                try:
                    with open(children_path, encoding="ascii") as fh:
                        raw = fh.read().strip()
                except (FileNotFoundError, PermissionError):
                    continue
                for token in raw.split():
                    try:
                        stack.append(int(token))
                    except ValueError:
                        continue
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            continue


def _walk(pid: int) -> list[int]:
    """Return [root, *descendants] for ``pid``. Always non-empty."""
    psutil = _try_import_psutil()
    if psutil is not None:
        descendants = list(_iter_descendants_psutil(pid, psutil))
    else:
        descendants = list(_iter_descendants_proc(pid))
    # Children before parent on suspend (stop kids first so the
    # parent doesn't fork new ones during the window); reverse for
    # resume — the caller controls order via ``reverse=`` flag.
    return [pid, *descendants]


def suspend_process_tree(pid: int) -> int:
    """Send STOP to ``pid`` and every descendant. Returns ack count.

    POSIX: ``os.kill(pid, signal.SIGSTOP)`` per PID — stdlib, no deps.
    Windows: ``psutil.Process(pid).suspend()`` per PID — psutil
    required. If psutil is missing on Windows the call logs an error
    and returns 0; the pause GUI button then surfaces a tooltip and
    the user can rely on cancel-and-resume instead.
    """
    if pid <= 0:
        return 0
    pids = _walk(pid)
    # Stop children first so a forking parent can't outrun the signal.
    pids = list(reversed(pids))
    return _apply(pids, action="suspend")


def resume_process_tree(pid: int) -> int:
    """Send CONT to ``pid`` and every descendant. Returns ack count.

    Parent first on resume so it can re-attach to its (still-stopped)
    children before they start running again. The symmetry with the
    reverse order in suspend is intentional.
    """
    if pid <= 0:
        return 0
    pids = _walk(pid)
    return _apply(pids, action="resume")


def _apply(pids: list[int], *, action: str) -> int:
    """Apply suspend/resume to each PID; swallow per-PID errors."""
    if not pids:
        return 0
    if _IS_WINDOWS:
        return _apply_windows(pids, action=action)
    return _apply_posix(pids, action=action)


def _apply_posix(pids: list[int], *, action: str) -> int:
    sig = signal.SIGSTOP if action == "suspend" else signal.SIGCONT
    ack = 0
    for p in pids:
        try:
            os.kill(p, sig)
            ack += 1
        except ProcessLookupError:
            # Race: child already exited between walk and signal.
            continue
        except PermissionError as exc:
            _log.warning(
                "process_control: %s pid=%s denied: %s", action, p, exc,
            )
        except OSError as exc:
            _log.warning(
                "process_control: %s pid=%s failed: %s", action, p, exc,
            )
    return ack


def _apply_windows(pids: list[int], *, action: str) -> int:
    psutil = _try_import_psutil()
    if psutil is None:
        _log.error(
            "process_control: %s on Windows requires the 'psutil' package; "
            "install it (`pip install psutil`) to enable pause/resume",
            action,
        )
        return 0
    ack = 0
    for p in pids:
        try:
            proc = psutil.Process(p)  # type: ignore[attr-defined]
            if action == "suspend":
                proc.suspend()
            else:
                proc.resume()
            ack += 1
        except Exception as exc:  # noqa: BLE001 — best-effort per pid
            # NoSuchProcess, AccessDenied, ZombieProcess — log + continue.
            _log.warning(
                "process_control: %s pid=%s failed: %s", action, p, exc,
            )
    return ack
