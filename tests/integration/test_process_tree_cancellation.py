"""Native-OS regression for cancelling a silent subprocess tree."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress

import pytest

from yt_uniquifier.core.runner import CancelToken, _run_once


def _pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        import psutil

        if not psutil.pid_exists(pid):
            return False
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    state = result.stdout.strip()
    return result.returncode == 0 and bool(state) and not state.startswith("Z")


@pytest.mark.integration
def test_cancel_terminates_silent_python_process_tree_on_native_os() -> None:
    """The CI Linux/Windows hosts must prove the grandchild exits too."""
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(f'grandchild_pid={child.pid}',flush=True); time.sleep(60)"
    )
    cancel_token = CancelToken()

    def cancel_soon() -> None:
        time.sleep(0.75)
        cancel_token.cancel()

    canceller = threading.Thread(target=cancel_soon, daemon=True)
    canceller.start()
    started = time.monotonic()
    returncode, log_lines = _run_once(
        [sys.executable, "-c", parent_code],
        on_event=lambda _event: None,
        cancel_token=cancel_token,
        proc_env=None,
    )
    elapsed = time.monotonic() - started
    canceller.join(timeout=2)

    pid_lines = [line for line in log_lines if line.startswith("grandchild_pid=")]
    assert len(pid_lines) == 1, log_lines
    grandchild_pid = int(pid_lines[0].partition("=")[2])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_is_running(grandchild_pid):
            time.sleep(0.05)
        assert not _pid_is_running(grandchild_pid), (
            f"cancel left grandchild pid {grandchild_pid} running on {sys.platform}"
        )
    finally:
        if _pid_is_running(grandchild_pid):
            if sys.platform == "win32":
                with suppress(Exception):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(grandchild_pid)],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
            else:
                with suppress(ProcessLookupError, PermissionError):
                    os.kill(grandchild_pid, signal.SIGKILL)

    assert returncode != 0
    assert elapsed < 10, f"process-tree cancellation took {elapsed:.2f}s"
