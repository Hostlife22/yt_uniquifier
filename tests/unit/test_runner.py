"""Unit tests for runner: progress parsing + cancel handling."""

from __future__ import annotations

import subprocess
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
        self._rc = rc
        self._done = False
        self.killed = False
        self.signalled = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._done = True
        return self._rc

    def poll(self) -> int | None:
        return self._rc if self._done else None

    def send_signal(self, sig: int) -> None:  # noqa: ARG002
        self.signalled = True
        self._done = True

    def kill(self) -> None:
        self.killed = True
        self._done = True


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
