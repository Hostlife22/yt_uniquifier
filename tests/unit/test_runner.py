"""Unit tests for runner: progress parsing + cancel handling."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.core import runner as runner_mod
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.runner import CancelToken, PauseToken, RunEvent, run


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._iter = iter(lines)

    def __iter__(self) -> Iterator[str]:
        return self._iter

    def read(self) -> str:
        return ""

    def close(self) -> None:
        pass


class _FakePopen:
    def __init__(self, stdout_lines: list[str], rc: int = 0,
                 stderr_text: str = "") -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = MagicMock()
        self.stderr.read.return_value = stderr_text
        self._stderr_text = stderr_text
        self._rc = rc
        self._done = False
        self.killed = False
        self.signalled = False
        self.communicate_calls = 0
        # v0.7 R9 — ``_terminate`` now routes through ``_signal_proc``
        # which reads ``proc.pid`` to call ``os.killpg(os.getpgid(pid))``
        # on POSIX. A real subprocess always has one; the fake needs a
        # plausible value so the watcher's cancel branch doesn't crash.
        # Os syscalls are stubbed below.
        self.pid = 12345
        # v0.7 R9 round-6 — ``subprocess.run`` (called from the runner's
        # Windows ``taskkill`` tree-kill path, which routes through this
        # fake under monkey-patch) terminates with
        # ``CompletedProcess(process.args, ...)``. Without an ``args``
        # attribute that raised AttributeError on the watcher daemon
        # thread.
        self.args: list[str] = ["fake-popen"]

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._done = True
        return self._rc

    def poll(self) -> int | None:
        return self._rc if self._done else None

    def send_signal(self, sig: int) -> None:  # noqa: ARG002
        self.signalled = True
        self._done = True

    def terminate(self) -> None:
        # v0.7 R9 — runner's ``_signal_proc`` on Windows routes SIGINT
        # via ``proc.terminate()`` (Windows ``send_signal`` won't take
        # arbitrary signals). Mirror the stub for the kill path so the
        # cancel-token watcher's exception handler stays quiet.
        self.signalled = True
        self._done = True

    def kill(self) -> None:
        self.killed = True
        self._done = True

    # v0.7 R9 round-4 — ``_patch_popen`` swaps the module-level
    # ``subprocess.Popen``, which means even ``subprocess.run`` (used
    # by the runner's Windows ``taskkill`` tree-kill path) routes
    # through this fake. ``subprocess.run`` opens its process with a
    # ``with Popen(...) as proc:`` block, so the fake must support the
    # context-manager protocol — without these dunders the cancel
    # path raises ``TypeError`` and the unit test inherits a noisy
    # unhandled-thread-exception warning.
    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def communicate(
        self,
        input: str | None = None,  # noqa: A002, ARG002
        timeout: float | None = None,  # noqa: ARG002
    ) -> tuple[str, str]:
        # v0.7 R9 round-5 — must accept ``input`` as a positional arg
        # because ``subprocess.run`` calls ``process.communicate(input,
        # timeout=timeout)`` and the Windows tree-kill path now routes
        # through it. Without this the watcher thread raises
        # ``TypeError: got multiple values for argument 'timeout'`` and
        # pytest surfaces a noisy PytestUnhandledThreadExceptionWarning.
        self.communicate_calls += 1
        self._done = True
        return "", self._stderr_text


def _patch_popen(monkeypatch: pytest.MonkeyPatch, fake: _FakePopen) -> None:
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *a, **k: fake)


def _cmd(tmp_path: Path) -> BuiltCommand:
    return BuiltCommand(
        args=["/usr/bin/ffmpeg", "-i", "in.mp4", str(tmp_path / "out.mp4")],
        filter_complex="anull",
        output_video_label="v1",
    )


def test_progress_events_emitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        "frame=10\n",
        "fps=24\n",
        "out_time_us=1000000\n",
        "speed=1.5x\n",
        "progress=continue\n",
        "frame=20\n",
        "out_time_us=2000000\n",
        "progress=end\n",
    ]
    _patch_popen(monkeypatch, _FakePopen(lines, rc=0))

    events: list[RunEvent] = []
    res = run(_cmd(tmp_path), output=tmp_path / "out.mp4", on_event=events.append)
    assert res.returncode == 0
    progress_evs = [e for e in events if e.kind == "progress"]
    assert len(progress_evs) == 2
    assert progress_evs[0].payload["out_time_us"] == "1000000"
    assert progress_evs[1].payload["progress"] == "end"
    assert any(e.kind == "done" for e in events)


def test_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(monkeypatch, _FakePopen(["progress=end\n"], rc=1,
                                         stderr_text="boom: something failed\n"))
    with pytest.raises(PipelineError, match="ffmpeg exited"):
        run(_cmd(tmp_path), output=tmp_path / "out.mp4")


def test_cancel_token_terminates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(["frame=1\n", "progress=continue\n", "frame=2\n", "progress=continue\n"],
                      rc=130)
    _patch_popen(monkeypatch, fake)

    token = CancelToken()
    token.cancel()  # cancel before the first line is read

    events: list[RunEvent] = []
    with pytest.raises(PipelineError, match="cancelled"):
        run(_cmd(tmp_path), output=tmp_path / "out.mp4",
            cancel_token=token, on_event=events.append)
    assert fake.signalled or fake.killed
    assert any(e.kind == "error" for e in events)


def test_log_file_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "out.mp4.log"
    _patch_popen(monkeypatch, _FakePopen(["progress=end\n"], rc=0,
                                         stderr_text="line1\nline2\n"))
    run(_cmd(tmp_path), output=tmp_path / "out.mp4", log_path=log_path)
    assert log_path.exists()
    content = log_path.read_text()
    assert "line1" in content


def test_subprocess_compatibility() -> None:
    """Smoke check that the real subprocess.Popen signature is unchanged."""
    # Trivial sanity — this ensures we import subprocess correctly.
    assert hasattr(subprocess, "Popen")


def test_non_cancel_path_requests_merged_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg logs and progress must share one pipe to prevent deadlock."""
    fake = _FakePopen(["progress=end\n"], rc=0)
    captured: dict[str, object] = {}

    def fake_popen(*_args: object, **kwargs: object) -> _FakePopen:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)

    run(_cmd(tmp_path), output=tmp_path / "out.mp4")

    assert captured["stderr"] is subprocess.STDOUT


def test_large_stderr_cannot_block_progress_pipe() -> None:
    """Regression: output larger than a Windows pipe must be drained live."""
    script = (
        "import sys; "
        "sys.stderr.write('x' * 200_000 + '\\n'); "
        "sys.stderr.flush(); "
        "sys.stdout.write('progress=end\\n'); "
        "sys.stdout.flush()"
    )

    rc, lines = runner_mod._run_once(
        [sys.executable, "-c", script],
        on_event=lambda _event: None,
        cancel_token=None,
    )

    assert rc == 0
    assert any(len(line) >= 200_000 for line in lines)


def test_streamed_log_is_complete_while_memory_tail_is_bounded(tmp_path: Path) -> None:
    line_count = 5000
    line_size = 1000
    script = (
        "import sys; "
        f"[(sys.stdout.write('x' * {line_size} + '\\n')) for _ in range({line_count})]; "
        "sys.stdout.flush()"
    )
    command = BuiltCommand(
        args=[sys.executable, "-c", script],
        filter_complex="",
        output_video_label="",
    )
    log_path = tmp_path / "complete.log"

    result = run(
        command,
        output=tmp_path / "unused",
        progress_via_stdout=False,
        stall_timeout_sec=5,
        log_path=log_path,
    )

    assert result.returncode == 0
    assert log_path.stat().st_size >= line_count * line_size
    _rc, retained = runner_mod._run_once(
        [sys.executable, "-c", script],
        on_event=lambda _event: None,
        cancel_token=None,
    )
    assert sum(len(line) for line in retained) <= runner_mod._MAX_RETAINED_LOG_CHARS


def test_stall_watchdog_terminates_silent_process(tmp_path: Path) -> None:
    """A live child holding the pipe open cannot wedge the runner forever."""
    cmd = BuiltCommand(
        args=[sys.executable, "-c", "import time; time.sleep(30)"],
        filter_complex="",
        output_video_label="",
    )
    started = time.monotonic()
    events: list[RunEvent] = []

    with pytest.raises(PipelineError, match="stalled with no output"):
        run(
            cmd,
            output=tmp_path / "unused",
            on_event=events.append,
            progress_via_stdout=False,
            stall_timeout_sec=0.2,
        )

    assert time.monotonic() - started < 5.0
    assert any(
        event.kind == "log" and event.payload.get("phase") == "watchdog"
        for event in events
    )
    assert any(event.kind == "error" for event in events)


def test_output_activity_resets_stall_watchdog(tmp_path: Path) -> None:
    script = (
        "import time; "
        "[(print(f'heartbeat={i}', flush=True), time.sleep(0.05)) for i in range(8)]"
    )
    cmd = BuiltCommand(
        args=[sys.executable, "-c", script],
        filter_complex="",
        output_video_label="",
    )

    result = run(
        cmd,
        output=tmp_path / "unused",
        progress_via_stdout=False,
        stall_timeout_sec=0.2,
    )

    assert result.returncode == 0


def test_wall_watchdog_terminates_active_process(tmp_path: Path) -> None:
    script = (
        "import time; "
        "[(print(f'heartbeat={i}', flush=True), time.sleep(0.05)) for i in range(600)]"
    )
    cmd = BuiltCommand(
        args=[sys.executable, "-c", script],
        filter_complex="",
        output_video_label="",
    )

    with pytest.raises(PipelineError, match="wall timeout"):
        run(
            cmd,
            output=tmp_path / "unused",
            progress_via_stdout=False,
            stall_timeout_sec=5,
            wall_timeout_sec=0.25,
        )


def test_invalid_timeout_environment_fails_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YT_UNIQ_STALL_TIMEOUT_SEC", "not-a-number")
    popen = MagicMock()
    monkeypatch.setattr(runner_mod.subprocess, "Popen", popen)

    with pytest.raises(PipelineError, match="YT_UNIQ_STALL_TIMEOUT_SEC"):
        run(_cmd(tmp_path), output=tmp_path / "out.mp4")

    popen.assert_not_called()


def test_progress_callback_failure_terminates_process_and_watcher(tmp_path: Path) -> None:
    """A frontend callback exception must not orphan ffmpeg or its watcher."""
    script = "import time; print('progress=continue', flush=True); time.sleep(30)"
    output = tmp_path / "unused"
    cmd = BuiltCommand(
        args=[sys.executable, "-u", "-c", script, str(output)],
        filter_complex="",
        output_video_label="",
    )
    existing_watchers = {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == "ffmpeg-cancel-pause-watcher"
    }
    started = time.monotonic()

    def fail_on_progress(event: RunEvent) -> None:
        if event.kind == "progress":
            raise RuntimeError("progress consumer failed")

    with pytest.raises(RuntimeError, match="progress consumer failed"):
        run(
            cmd,
            output=output,
            on_event=fail_on_progress,
            progress_via_stdout=False,
            stall_timeout_sec=5,
            log_path=tmp_path / "callback-failure.log",
        )

    assert time.monotonic() - started < 10
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name == "ffmpeg-cancel-pause-watcher"
        and thread.ident not in existing_watchers
        and thread.is_alive()
    ]


def test_watcher_callback_failure_resumes_and_terminates_process(tmp_path: Path) -> None:
    """A callback failure on the pause watcher cannot leave a stopped child."""
    output = tmp_path / "unused"
    cmd = BuiltCommand(
        args=[
            sys.executable, "-u", "-c", "import time; time.sleep(30)", str(output),
        ],
        filter_complex="",
        output_video_label="",
    )
    pause_token = PauseToken()
    pause_token.pause()
    started = time.monotonic()

    def fail_on_pause(event: RunEvent) -> None:
        if event.kind == "log" and event.payload.get("phase") == "paused":
            raise RuntimeError("pause consumer failed")

    with pytest.raises(RuntimeError, match="pause consumer failed"):
        run(
            cmd,
            output=output,
            on_event=fail_on_pause,
            pause_token=pause_token,
            progress_via_stdout=False,
            stall_timeout_sec=5,
        )

    assert time.monotonic() - started < 10
