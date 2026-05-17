"""runner.run() NVENC OOM retry logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.core import runner as runner_mod
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.runner import _is_nvenc_oom, run


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._iter = iter(lines)

    def __iter__(self):
        return self._iter

    def read(self) -> str:
        return ""

    def close(self) -> None:
        pass


class _FakePopen:
    def __init__(self, stdout_lines: list[str], rc: int,
                 stderr_text: str) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = MagicMock()
        self.stderr.read.return_value = stderr_text
        self._rc = rc
        self._done = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._done = True
        return self._rc

    def poll(self) -> int | None:
        return self._rc if self._done else None

    def send_signal(self, _sig: int) -> None:
        self._done = True

    def kill(self) -> None:
        self._done = True


def _cmd(tmp_path: Path) -> BuiltCommand:
    return BuiltCommand(
        args=["/usr/bin/ffmpeg", "-i", "in.mp4", str(tmp_path / "out.mp4")],
        filter_complex="anull",
        output_video_label="v1",
    )


# ---- _is_nvenc_oom --------------------------------------------------------

def test_oom_pattern_detected() -> None:
    assert _is_nvenc_oom([
        "frame=1",
        "[h264_nvenc @ 0x] OpenEncodeSessionEx failed: out of memory (10)",
    ]) is True


def test_oom_case_insensitive() -> None:
    assert _is_nvenc_oom(["openencodesessionex FAILED"]) is True


def test_non_oom_error_not_detected() -> None:
    assert _is_nvenc_oom([
        "frame=1",
        "[libx264 @ 0x] width not divisible by 2 (319x180)",
    ]) is False


def test_empty_log_not_oom() -> None:
    assert _is_nvenc_oom([]) is False


def test_no_capable_devices_detected() -> None:
    assert _is_nvenc_oom(["[h264_nvenc] No NVENC capable devices found"]) is True


# ---- run() retry behaviour ------------------------------------------------

def test_nvenc_oom_retries_once(tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """First call returns OOM; second call succeeds."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_popen(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakePopen(
                ["progress=end"], rc=1,
                stderr_text="OpenEncodeSessionEx failed: out of memory\n",
            )
        return _FakePopen(["progress=end"], rc=0, stderr_text="")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_mod.time, "sleep", lambda s: sleeps.append(s))

    res = run(_cmd(tmp_path), output=tmp_path / "out.mp4")
    assert res.returncode == 0
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_nvenc_oom_retry_only_once(tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call ALSO OOMs → propagate, no infinite loop."""
    calls = {"n": 0}

    def fake_popen(*_args, **_kwargs):
        calls["n"] += 1
        return _FakePopen(
            ["progress=end"], rc=1,
            stderr_text="OpenEncodeSessionEx failed: out of memory\n",
        )

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PipelineError, match="ffmpeg exited"):
        run(_cmd(tmp_path), output=tmp_path / "out.mp4")
    assert calls["n"] == 2


def test_non_oom_error_no_retry(tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Regular error (e.g. unknown encoder) is not OOM → no retry."""
    calls = {"n": 0}

    def fake_popen(*_args, **_kwargs):
        calls["n"] += 1
        return _FakePopen(["progress=end"], rc=1,
                          stderr_text="width not divisible by 2\n")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PipelineError):
        run(_cmd(tmp_path), output=tmp_path / "out.mp4")
    assert calls["n"] == 1
