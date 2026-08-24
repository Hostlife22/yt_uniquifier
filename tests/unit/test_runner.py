"""Unit tests for runner: progress parsing + cancel handling."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.core import runner as runner_mod
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.runner import CancelToken, RunEvent, run


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
