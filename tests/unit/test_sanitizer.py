"""v0.4.3 bitstream sanitization — second-pass libx264 normalization."""

from __future__ import annotations

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import EncoderCandidate
from yt_uniquifier.core.sanitizer import needs_sanitization, reject_for_hdr_or_hevc


def _enc(name: str, vendor: str, codec: str = "h264") -> EncoderCandidate:
    return EncoderCandidate(name=name, vendor=vendor, codec=codec, works=True)


def test_needs_sanitization_for_nvenc() -> None:
    assert needs_sanitization(_enc("h264_nvenc", "nvenc")) is True


def test_needs_sanitization_for_qsv() -> None:
    assert needs_sanitization(_enc("h264_qsv", "qsv")) is True


def test_needs_sanitization_for_amf() -> None:
    assert needs_sanitization(_enc("h264_amf", "amf")) is True


def test_needs_sanitization_for_videotoolbox() -> None:
    assert needs_sanitization(_enc("h264_videotoolbox", "videotoolbox")) is True


def test_no_sanitization_for_x264() -> None:
    """libx264-source output already has the modal bitstream signature."""
    assert needs_sanitization(_enc("libx264", "x264")) is False


def test_reject_for_keep_hdr() -> None:
    """HDR keep-hdr → libx264 has no 10-bit profile → refuse explicitly."""
    enc = _enc("libx264", "x264")
    with pytest.raises(PipelineError, match="keep_hdr"):
        reject_for_hdr_or_hevc(plan_keep_hdr=True, encoder=enc)


def test_reject_for_hevc() -> None:
    """HEVC source through libx264 re-encode is usually not the intent."""
    enc = _enc("hevc_nvenc", "nvenc", codec="hevc")
    with pytest.raises(PipelineError, match="hevc"):
        reject_for_hdr_or_hevc(plan_keep_hdr=False, encoder=enc)


def test_no_reject_for_sdr_h264() -> None:
    """libx264 + SDR is the expected good case — no error."""
    enc = _enc("libx264", "x264")
    # Should not raise.
    reject_for_hdr_or_hevc(plan_keep_hdr=False, encoder=enc)


# ---------------------------------------------------------------------------
# v1.0.0 R3 — coverage for sanitize_bitstream() + _terminate(). The real
# integration test (`tests/integration/test_sanitize_real_ffmpeg.py`) runs
# ffmpeg end-to-end; here we only need the control-flow branches to be
# exercised under a faked Popen so the unit suite stays fast and works
# without a real video decoder.
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from yt_uniquifier.core import sanitizer  # noqa: E402
from yt_uniquifier.core.runner import CancelToken  # noqa: E402


class _FakePopen:
    """Minimal Popen stand-in for sanitize_bitstream's poll loop.

    Behaviour matrix:
      ``never_exits=True`` → poll/wait stay pending until the test
        flips ``signalled`` or calls ``kill``; communicate then
        returns the configured ``stderr_text``.
      ``never_exits=False`` → first ``wait`` raises TimeoutExpired
        (so the cancel-token branch can fire), subsequent waits return
        ``rc``; communicate yields the configured stderr.
    """

    def __init__(
        self,
        *,
        rc: int = 0,
        stderr_text: str = "",
        never_exits: bool = False,
    ) -> None:
        self._rc = rc
        self._stderr = stderr_text
        self._never_exits = never_exits
        self._wait_calls = 0
        self.signalled = False
        self.killed = False
        self.pid = 4242

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._wait_calls += 1
        # First wait always times out so the polling loop runs at
        # least once — gives the cancel/deadline branches a window.
        if self._wait_calls == 1 or self._never_exits:
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=timeout or 0.5)
        return self._rc

    def poll(self) -> int | None:
        # never_exits → stay running until kill() lands. SIGINT alone
        # does not "convince" this fake to exit; the production
        # _terminate must therefore escalate to kill.
        if self._never_exits and not self.killed:
            return None
        return self._rc

    def send_signal(self, _sig: int) -> None:
        self.signalled = True

    def kill(self) -> None:
        self.killed = True

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:  # noqa: ARG002
        return ("", self._stderr)

    @property
    def returncode(self) -> int:
        return self._rc


def test_sanitize_bitstream_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(PipelineError, match="input not found"):
        sanitizer.sanitize_bitstream(missing, tmp_path / "out.mp4")


def test_sanitize_bitstream_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful sanitize: fake Popen exits with rc=0, tmp renamed to output."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"

    tmp_target = out.with_suffix(".sanitized.mp4")

    def _fake_popen(cmd: list[str], **_kw: object) -> _FakePopen:
        # Production code creates the tmp file via ffmpeg; the fake
        # writes a stub so the final .replace() has a real source.
        tmp_target.write_bytes(b"FAKEOUT")
        return _FakePopen(rc=0)

    monkeypatch.setattr(sanitizer.subprocess, "Popen", _fake_popen)

    sanitizer.sanitize_bitstream(src, out)
    assert out.exists()
    assert not tmp_target.exists()  # renamed away


def test_sanitize_bitstream_failure_surfaces_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"
    tmp_target = out.with_suffix(".sanitized.mp4")

    def _fake_popen(cmd: list[str], **_kw: object) -> _FakePopen:
        tmp_target.write_bytes(b"PARTIAL")
        return _FakePopen(rc=1, stderr_text="ffmpeg: codec not found")

    monkeypatch.setattr(sanitizer.subprocess, "Popen", _fake_popen)

    with pytest.raises(PipelineError, match="codec not found"):
        sanitizer.sanitize_bitstream(src, out)
    assert not tmp_target.exists()  # cleaned up on failure
    assert not out.exists()


def test_sanitize_bitstream_cancel_via_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"
    tmp_target = out.with_suffix(".sanitized.mp4")

    token = CancelToken()
    token.cancel()  # fire before Popen starts

    captured: dict[str, _FakePopen] = {}

    def _fake_popen(cmd: list[str], **_kw: object) -> _FakePopen:
        tmp_target.write_bytes(b"PARTIAL")
        p = _FakePopen(rc=0, never_exits=True)
        captured["proc"] = p
        return p

    monkeypatch.setattr(sanitizer.subprocess, "Popen", _fake_popen)

    with pytest.raises(PipelineError, match="cancelled by user"):
        sanitizer.sanitize_bitstream(src, out, cancel_token=token)
    assert captured["proc"].signalled is True
    assert not tmp_target.exists()
    assert not out.exists()


def test_internal_terminate_skips_when_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_terminate is a no-op if poll() already shows the process exited."""

    class _Exited:
        def poll(self) -> int:
            return 0

        def send_signal(self, _sig: int) -> None:  # pragma: no cover
            raise AssertionError("send_signal must not be called")

        def kill(self) -> None:  # pragma: no cover
            raise AssertionError("kill must not be called")

    sanitizer._terminate(_Exited())  # type: ignore[arg-type]


def test_internal_terminate_escalates_to_kill_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If SIGINT does not land before the grace deadline, _terminate must call kill."""
    proc = _FakePopen(never_exits=True)
    # Tight grace so the test does not slow the suite.
    sanitizer._terminate(proc, wait_sec=0.05)  # type: ignore[arg-type]
    assert proc.signalled is True
    assert proc.killed is True
