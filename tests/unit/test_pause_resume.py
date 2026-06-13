"""v0.7 R6 / F5 — pause / resume unit coverage.

Tested seams:

  * ``PauseToken`` state machine: idempotent ``pause()`` / ``resume()``,
    monotonic age tracking, 24-hour auto-cancel safety, and the
    ``wait_while_paused`` loop honouring both resume and cancel.
  * ``process_control.suspend_process_tree`` / ``resume_process_tree``
    contract: returns 0 on bogus PID, swallows per-PID errors, and
    routes through SIGSTOP / SIGCONT (POSIX) or psutil.suspend
    (Windows). End-to-end signal delivery is verified against a real
    ``sleep`` child process on POSIX hosts.
  * Runner watcher thread fires SIGSTOP on pause and SIGCONT on resume
    against a mocked ``Popen``.
  * Checkpoint ``set_paused_at`` round-trip — UTC ISO-8601, ``None``
    clears the marker, debounced flushes do not drop the write.
  * Orchestrator's pause observer: writes & clears ``paused_at`` on
    transitions and triggers ``cancel_token.cancel()`` past the 24h
    threshold.

The integration test that drives a real ffmpeg subprocess under
SIGSTOP lives in tests/integration/test_pause_resume_real_ffmpeg.py
(marked ``@pytest.mark.integration``) — these unit tests intentionally
avoid spawning ffmpeg so they run in <2 s.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_uniquifier.core.runner import CancelToken, PauseToken

# ---- PauseToken state machine ---------------------------------------------

def test_pause_token_default_is_running() -> None:
    t = PauseToken()
    assert not t.is_paused()
    assert t.paused_for_sec() == 0.0
    assert t.paused_at_wall() is None
    assert not t.should_auto_cancel()


def test_pause_then_resume_clears_marker() -> None:
    t = PauseToken()
    t.pause()
    assert t.is_paused()
    assert t.paused_at_wall() is not None
    assert t.paused_for_sec() >= 0.0
    t.resume()
    assert not t.is_paused()
    assert t.paused_at_wall() is None


def test_pause_is_idempotent_keeps_first_timestamp() -> None:
    """Two pause() calls in a row must not bump the monotonic anchor."""
    t = PauseToken()
    t.pause()
    first_wall = t.paused_at_wall()
    time.sleep(0.01)
    t.pause()
    assert t.paused_at_wall() == first_wall


def test_resume_is_idempotent() -> None:
    t = PauseToken()
    t.resume()  # not paused yet
    assert not t.is_paused()
    t.pause()
    t.resume()
    t.resume()  # second resume is a no-op
    assert not t.is_paused()


def test_paused_for_sec_advances() -> None:
    t = PauseToken()
    t.pause()
    time.sleep(0.05)
    assert t.paused_for_sec() >= 0.04  # allow a little jitter
    t.resume()
    assert t.paused_for_sec() == 0.0


def test_should_auto_cancel_respects_threshold() -> None:
    t = PauseToken()
    t.pause()
    # Real clock would take 24h. Patch the monotonic anchor instead.
    with t._lock:
        t._paused_at_monotonic = time.monotonic() - PauseToken.AUTO_CANCEL_SEC - 1
    assert t.should_auto_cancel()


def test_wait_while_paused_returns_on_resume() -> None:
    t = PauseToken()
    t.pause()

    def _resumer() -> None:
        time.sleep(0.05)
        t.resume()

    threading.Thread(target=_resumer, daemon=True).start()
    cancelled = t.wait_while_paused(poll_sec=0.01)
    assert cancelled is False
    assert not t.is_paused()


def test_wait_while_paused_returns_on_cancel() -> None:
    t = PauseToken()
    ct = CancelToken()
    t.pause()

    def _canceller() -> None:
        time.sleep(0.05)
        ct.cancel()

    threading.Thread(target=_canceller, daemon=True).start()
    cancelled = t.wait_while_paused(cancel_token=ct, poll_sec=0.01)
    assert cancelled is True


def test_wait_while_paused_auto_cancels_at_threshold() -> None:
    t = PauseToken()
    ct = CancelToken()
    t.pause()
    # Force the age past the auto-cancel threshold.
    with t._lock:
        t._paused_at_monotonic = time.monotonic() - PauseToken.AUTO_CANCEL_SEC - 1
    assert t.wait_while_paused(cancel_token=ct, poll_sec=0.01) is True
    assert ct.is_cancelled()


# ---- process_control: API contract ----------------------------------------

def test_suspend_process_tree_zero_pid_returns_zero() -> None:
    from yt_uniquifier.core.process_control import suspend_process_tree
    assert suspend_process_tree(0) == 0
    assert suspend_process_tree(-7) == 0


def test_resume_process_tree_zero_pid_returns_zero() -> None:
    from yt_uniquifier.core.process_control import resume_process_tree
    assert resume_process_tree(0) == 0


def test_suspend_swallows_lookup_error_on_dead_pid() -> None:
    """Walking a tree where a child died mid-walk must not raise."""
    from yt_uniquifier.core.process_control import _apply_posix
    if sys.platform.startswith("win"):
        pytest.skip("POSIX-only path")
    # 999999 should be safely "no such process" on any sane box.
    # _apply silences ProcessLookupError per PID; ack count = 0.
    assert _apply_posix([999_999], action="suspend") == 0


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX signal delivery; Windows uses psutil path",
)
def test_suspend_and_resume_against_real_child() -> None:
    """End-to-end: SIGSTOP + SIGCONT against a real sleeping child.

    Verifies process_control actually drives os.kill — a regression
    that silently no-op'd would otherwise pass unit tests but fail
    in production.
    """
    from yt_uniquifier.core.process_control import (
        resume_process_tree,
        suspend_process_tree,
    )
    proc = subprocess.Popen(["sleep", "5"])
    try:
        assert suspend_process_tree(proc.pid) >= 1
        # Give the kernel a moment to apply the stop state.
        time.sleep(0.05)
        # The process is alive but stopped; SIGCONT must wake it.
        assert resume_process_tree(proc.pid) >= 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---- runner.run watcher: SIGSTOP / SIGCONT on transitions -----------------

def _make_fake_proc(pid: int = 12345) -> MagicMock:
    """A Popen stand-in for the watcher thread: alive until poll() != None."""
    fake = MagicMock()
    fake.pid = pid
    fake.poll.return_value = None       # alive
    fake.returncode = None
    fake.stdout = iter([])              # the for-line loop exits immediately
    fake.stderr = None
    return fake


def test_runner_watcher_suspends_on_pause_and_resumes() -> None:
    """Drive the watcher loop directly via patched suspend/resume helpers.

    The full ``_run_once`` body needs a real ffmpeg subprocess to drain
    stdout. To stay in the unit layer, we test the watcher in isolation
    by constructing the same closure the runner builds.
    """
    from yt_uniquifier.core import runner as _runner
    pause_token = PauseToken()
    cancel_token = CancelToken()
    fake = _make_fake_proc()
    stop = threading.Event()

    suspend_calls: list[int] = []
    resume_calls: list[int] = []

    def _suspend(pid: int) -> bool:
        suspend_calls.append(pid)
        return True

    def _resume(pid: int) -> bool:
        resume_calls.append(pid)
        return True

    with (
        patch.object(_runner, "_suspend_pid_safe", side_effect=_suspend),
        patch.object(_runner, "_resume_pid_safe", side_effect=_resume),
    ):
        # Reconstruct the watcher body. (Copy of the runtime closure —
        # if the runner refactors this loop the test must follow.)
        def _watch() -> None:
            suspended = False
            while not stop.is_set():
                if cancel_token.is_cancelled():
                    if suspended:
                        _runner._resume_pid_safe(fake.pid)
                    return
                if fake.poll() is not None:
                    return
                want = pause_token.is_paused()
                if want and not suspended and _runner._suspend_pid_safe(fake.pid):
                    suspended = True
                elif (
                    not want and suspended
                    and _runner._resume_pid_safe(fake.pid)
                ):
                    suspended = False
                if cancel_token.wait(0.02):
                    continue

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()
        time.sleep(0.05)
        pause_token.pause()
        # Give the watcher time to observe the pause.
        for _ in range(50):
            if suspend_calls:
                break
            time.sleep(0.01)
        assert suspend_calls == [fake.pid]

        pause_token.resume()
        for _ in range(50):
            if resume_calls:
                break
            time.sleep(0.01)
        assert resume_calls == [fake.pid]

        stop.set()
        cancel_token.cancel()
        thread.join(timeout=1.0)
        assert not thread.is_alive()


# ---- checkpoint.set_paused_at ----------------------------------------------

def _stub_plan(tmp_path: Path) -> object:
    """Build a minimal Plan stand-in for CheckpointStore.

    CheckpointStore reads ``plan.plan_hash`` and ``plan.run_seed``;
    everything else is irrelevant to the paused_at round-trip.
    """
    plan = MagicMock()
    plan.plan_hash = "abcdef" * 8
    plan.run_seed = 12345
    return plan


def test_checkpoint_set_paused_at_writes_iso_utc(tmp_path: Path) -> None:
    from yt_uniquifier.core.checkpoint import CheckpointStore
    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    store.set_paused_at(1_700_000_000.0)
    raw = store.get_paused_at()
    assert raw is not None and raw.endswith("+00:00")
    assert "2023-11-14" in raw  # 1.7e9 → 2023-11-14 UTC
    store.close()


def test_checkpoint_set_paused_at_clears_marker(tmp_path: Path) -> None:
    from yt_uniquifier.core.checkpoint import CheckpointStore
    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    store.set_paused_at(1_700_000_000.0)
    assert store.get_paused_at() is not None
    store.set_paused_at(None)
    assert store.get_paused_at() is None
    store.close()


def test_checkpoint_paused_at_survives_reload(tmp_path: Path) -> None:
    """A crash mid-pause must leave a visible artefact on disk."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    store.set_paused_at(1_700_000_000.0)
    store.close()

    # Re-open with the same plan hash → state.json carries paused_at.
    store2 = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store2.init_or_resume([])
    assert store2.get_paused_at() is not None
    store2.close()


# ---- orchestrator pause observer ------------------------------------------

def test_pause_observer_persists_and_clears(tmp_path: Path) -> None:
    """The observer thread writes paused_at on pause and clears on resume."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.orchestrator import _start_pause_observer

    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    pause_token = PauseToken()
    cancel_token = CancelToken()
    logged: list[object] = []

    def _emit(ev: object) -> None:
        logged.append(ev)

    stop = _start_pause_observer(pause_token, cancel_token, store, _emit)
    try:
        pause_token.pause()
        for _ in range(40):
            if store.get_paused_at() is not None:
                break
            time.sleep(0.05)
        assert store.get_paused_at() is not None

        pause_token.resume()
        for _ in range(40):
            if store.get_paused_at() is None:
                break
            time.sleep(0.05)
        assert store.get_paused_at() is None
    finally:
        stop.set()
        store.close()


def test_pause_observer_no_token_is_noop(tmp_path: Path) -> None:
    """``pause_token=None`` returns an unset Event and never spawns a thread."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.orchestrator import _start_pause_observer

    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    stop = _start_pause_observer(None, None, store, lambda _ev: None)
    assert isinstance(stop, threading.Event)
    assert not stop.is_set()
    store.close()


def test_pause_observer_triggers_auto_cancel_past_threshold(tmp_path: Path) -> None:
    """Past 24h the observer must fire cancel_token.cancel()."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.orchestrator import _start_pause_observer

    store = CheckpointStore(tmp_path, _stub_plan(tmp_path))
    store.init_or_resume([])
    pause_token = PauseToken()
    cancel_token = CancelToken()
    pause_token.pause()
    # Backdate so should_auto_cancel() trips on the next poll.
    with pause_token._lock:
        pause_token._paused_at_monotonic = (
            time.monotonic() - PauseToken.AUTO_CANCEL_SEC - 1
        )

    stop = _start_pause_observer(
        pause_token, cancel_token, store, lambda _ev: None,
    )
    try:
        for _ in range(40):
            if cancel_token.is_cancelled():
                break
            time.sleep(0.05)
        assert cancel_token.is_cancelled()
    finally:
        stop.set()
        store.close()
