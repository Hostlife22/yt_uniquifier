"""A5 (v0.5.5) regression: ``_run_once`` honours cancel even when
ffmpeg produces no stdout.

Pre-fix the main stdout-line loop only checked ``cancel_token.is_cancelled()``
between progress lines. A hung NVENC session or libx264 final-flush
stage that emits no progress would ignore cancel for up to 3600 s
(the outer ``communicate`` timeout).

Post-fix a daemon watcher thread polls ``cancel_token.wait(0.25)``
alongside Popen and SIGTERMs the child regardless of stdout state.
"""

from __future__ import annotations

import shutil
import threading
import time

import pytest

from yt_uniquifier.core.runner import CancelToken, _run_once

needs_sh = pytest.mark.skipif(
    shutil.which("sh") is None,
    reason="POSIX sh required for hung-subprocess simulation",
)


@needs_sh
def test_cancel_during_silent_subprocess_terminates_fast() -> None:
    """Spawn a subprocess that sleeps 30s without producing any
    stdout. Fire cancel 0.3s in. The watcher must terminate the child
    within ~1s, not wait for the 30s sleep to complete.
    """
    cancel_token = CancelToken()

    def fire_cancel() -> None:
        time.sleep(0.3)
        cancel_token.cancel()

    canceller = threading.Thread(target=fire_cancel, daemon=True)
    canceller.start()

    start = time.monotonic()
    rc, log_lines = _run_once(
        ["sh", "-c", "sleep 30"],
        on_event=lambda _e: None,
        cancel_token=cancel_token,
        proc_env=None,
    )
    elapsed = time.monotonic() - start
    canceller.join(timeout=1.0)

    # Without A5 this takes ~30s (sleep) or up to 3600s (communicate).
    # With A5: cancel detected within 250ms, SIGINT + 5s _terminate
    # grace window, total well under 7s on any sane machine.
    assert elapsed < 7.0, (
        f"watcher did not terminate hung subprocess fast enough; "
        f"elapsed={elapsed:.2f}s"
    )
    # rc is non-zero (terminated by signal). Exact value depends on
    # platform; just assert "not success".
    assert rc != 0, f"expected non-zero rc for terminated subprocess, got {rc}"


@needs_sh
def test_watcher_does_not_terminate_normal_run() -> None:
    """A subprocess that exits normally before cancel fires must not
    be killed by the watcher. Watcher should exit when stop_watcher is
    set, leaving the natural exit code intact.
    """
    cancel_token = CancelToken()

    start = time.monotonic()
    rc, _ = _run_once(
        ["sh", "-c", "echo done"],
        on_event=lambda _e: None,
        cancel_token=cancel_token,
        proc_env=None,
    )
    elapsed = time.monotonic() - start

    assert rc == 0, f"natural exit should yield rc=0, got {rc}"
    assert elapsed < 2.0, (
        f"normal short subprocess took too long; elapsed={elapsed:.2f}s"
    )
    # Cancel was never fired — token still clean.
    assert not cancel_token.is_cancelled()


def test_no_watcher_started_when_cancel_token_is_none() -> None:
    """Backward compatibility: callers that don't pass a cancel_token
    must continue to work, with no watcher thread spawned at all.

    We exercise the path with a fake subprocess shell — the existing
    ``run`` path passes ``cancel_token=None`` from several CLI commands,
    so this guard is load-bearing.
    """
    threads_before = threading.active_count()
    rc, _ = _run_once(
        ["sh", "-c", "true"] if shutil.which("sh") else ["true"],
        on_event=lambda _e: None,
        cancel_token=None,
        proc_env=None,
    )
    threads_after = threading.active_count()
    assert rc == 0
    # No watcher thread should linger after _run_once returns.
    assert threads_after <= threads_before + 1, (
        "watcher thread leaked (or some other thread was started)"
    )
