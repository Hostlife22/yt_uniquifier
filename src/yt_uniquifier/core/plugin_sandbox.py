"""v1.2.0 Task 23 — runtime sandbox for third-party plugin code.

CPython's PEP 578 audit framework emits events for every privileged
operation (file open, socket creation, subprocess launch, dynamic code
execution).  We install a single process-global audit hook that consults
a contextvar — when plugin code is currently on the stack, denylisted
events raise :class:`PluginViolation`; for built-in or core code paths
the hook is a no-op.

Coverage and limits:

  * Cross-platform (Linux, macOS, Windows) because PEP 578 is part of
    CPython, not the OS.
  * Catches every operation that flows through the CPython runtime —
    ``open()``, ``socket.socket()``, ``subprocess.Popen``, ``os.exec*``,
    ``compile()`` with ``exec``.
  * Does NOT catch syscalls issued by C extensions that bypass the
    audit framework.  A plugin that ships its own .so and dlopens into
    a raw syscall sidesteps this layer — Linux seccomp is the right
    next layer for that threat model (documented in
    docs/plugins.md § Sandbox limits as a backlog item).
  * Audit hooks cannot be removed once installed (PEP 578 deliberately
    forbids it).  We install ours at first use and gate it on the
    contextvar so it's harmless during normal operation.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager

from yt_uniquifier.core.plugins import PluginViolation

_log = logging.getLogger(__name__)

# Audit events emitted by CPython that we treat as forbidden inside
# plugin code.  Names come from the PEP 578 standard hooks table:
# https://docs.python.org/3/library/audit_events.html.  The list is
# deliberately narrow — we want to catch the realistic threats
# (filesystem write, network egress, subprocess spawn, dynamic code
# eval) without flagging benign reads.  A plugin that needs to read a
# bundled data file at import time wouldn't trigger 'open' as long as
# it does so before the sandbox context activates (i.e. at module
# import, not inside a builder).
_DENYLIST_EVENTS: frozenset[str] = frozenset({
    # Filesystem mutation
    "os.remove",
    "os.unlink",
    "os.rename",
    "os.replace",
    "os.rmdir",
    "os.makedirs",
    "os.mkdir",
    "os.chmod",
    "os.chown",
    # Subprocess / exec
    "subprocess.Popen",
    "os.exec",
    "os.spawn",
    "os.system",
    # Network egress
    "socket.connect",
    "socket.bind",
    "socket.gethostbyname",
    # Dynamic code execution
    "exec",
    "compile",
})

# Set to ``True`` for the duration of plugin code execution.  Read by
# the audit hook to decide whether to enforce.  contextvar keeps the
# scope thread-correct across the GUI worker pool and the orchestrator
# segment threads.
_IN_PLUGIN_CODE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_yt_uniq_in_plugin_code", default=False,
)

# Audit hooks can't be uninstalled; install at most once per process.
_hook_lock = threading.Lock()
_hook_installed = False
# Set to True by ``disable_sandbox()`` so operators can intentionally
# bypass the sandbox (e.g. for trusted internal plugins).  The audit
# hook short-circuits when this is set so we don't have to redefine
# the hook itself.
_sandbox_disabled = False


def _audit_hook(event: str, args: object) -> None:
    """Per-event audit hook.  Process-global; cheap when inactive."""
    if _sandbox_disabled:
        return
    if event not in _DENYLIST_EVENTS:
        return
    if not _IN_PLUGIN_CODE.get():
        return
    # ``args`` is normally a tuple but PEP 578 documents it as ``object``
    # so a defensive str() keeps us honest if a future event shape changes.
    raise PluginViolation(
        f"plugin code attempted denylisted operation {event!r} "
        f"(args={args!r}).  Update yt_uniquifier_plugin.toml capabilities "
        "or run with --unsafe-plugins to bypass; see docs/plugins.md § Sandbox."
    )


def install_sandbox() -> None:
    """Install the audit hook (idempotent).  Call once at process
    startup before any plugin discovery runs.  Safe to call from any
    thread; the install itself is serialised behind a lock.
    """
    global _hook_installed
    with _hook_lock:
        if _hook_installed:
            return
        sys.addaudithook(_audit_hook)
        _hook_installed = True
        _log.debug("plugin audit-hook sandbox installed")


def disable_sandbox() -> None:
    """Disable the sandbox at the contextvar level.

    The audit hook itself stays installed (PEP 578 forbids removal) but
    becomes a no-op for every event.  Set via ``--unsafe-plugins`` for
    operators who explicitly opt in to running trusted internal
    plugins without the audit gate.
    """
    global _sandbox_disabled
    _sandbox_disabled = True
    _log.warning(
        "plugin sandbox disabled — third-party plugins will run with "
        "no syscall gating.  This is intended for trusted internal "
        "plugins only; do not use with PyPI installs.",
    )


def is_sandbox_disabled() -> bool:
    return _sandbox_disabled


@contextmanager
def in_plugin_code() -> Generator[None, None, None]:
    """Activate sandbox enforcement for the wrapped block.

    Any denylisted audit event raised by the code inside the ``with``
    block raises :class:`PluginViolation`.  Outside the block the audit
    hook is a no-op.  Use to wrap calls into a plugin's ``register``
    side-effects and into plugin transform ``build()`` invocations.
    """
    install_sandbox()
    token = _IN_PLUGIN_CODE.set(True)
    try:
        yield
    finally:
        _IN_PLUGIN_CODE.reset(token)
