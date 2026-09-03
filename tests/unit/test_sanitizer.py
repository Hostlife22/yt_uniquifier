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
    """A libx264 output already satisfies the normalization contract."""
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


def test_reject_for_av1() -> None:
    """The optional H.264 pass must not silently change an AV1 contract."""
    enc = _enc("libsvtav1", "svtav1", codec="av1")
    with pytest.raises(PipelineError, match="av1"):
        reject_for_hdr_or_hevc(plan_keep_hdr=False, encoder=enc)


def test_no_reject_for_sdr_h264() -> None:
    """libx264 + SDR is the expected good case — no error."""
    enc = _enc("libx264", "x264")
    # Should not raise.
    reject_for_hdr_or_hevc(plan_keep_hdr=False, encoder=enc)


# ---------------------------------------------------------------------------
# Coverage for sanitize_bitstream(). The real
# integration test (`tests/integration/test_sanitize_real_ffmpeg.py`) runs
# ffmpeg end-to-end; here we only need command/cleanup behavior to be
# exercised under a faked shared runner so the unit suite stays fast and works
# without a real video decoder.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from yt_uniquifier.core import sanitizer  # noqa: E402
from yt_uniquifier.core.runner import CancelToken, RunResult  # noqa: E402


def test_sanitize_bitstream_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(PipelineError, match="input not found"):
        sanitizer.sanitize_bitstream(missing, tmp_path / "out.mp4")


def test_sanitize_bitstream_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful sanitize uses the shared watchdog runner then atomically renames."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"

    captured: dict[str, Any] = {}

    def _fake_run(cmd, *, output, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        captured["output"] = output
        output.write_bytes(b"FAKEOUT")
        return RunResult(returncode=0, duration_sec=1.0, output_path=output)

    monkeypatch.setattr(sanitizer, "run_ffmpeg", _fake_run)

    sanitizer.sanitize_bitstream(src, out)
    assert out.exists()
    assert not captured["output"].exists()  # renamed away
    assert "-map_metadata" in captured["cmd"].args
    metadata_at = captured["cmd"].args.index("-map_metadata")
    assert captured["cmd"].args[metadata_at + 1] == "0"


def test_sanitize_bitstream_failure_surfaces_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"
    captured: dict[str, Path] = {}

    def _fake_run(*_args: object, **kwargs: Any) -> RunResult:
        captured["output"] = kwargs["output"]
        captured["output"].write_bytes(b"PARTIAL")
        raise PipelineError("ffmpeg: codec not found")

    monkeypatch.setattr(sanitizer, "run_ffmpeg", _fake_run)

    with pytest.raises(PipelineError, match="codec not found"):
        sanitizer.sanitize_bitstream(src, out)
    assert not captured["output"].exists()  # cleaned up on failure
    assert not out.exists()


def test_sanitize_bitstream_cancel_via_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mp4"
    captured: dict[str, Path] = {}

    token = CancelToken()
    token.cancel()  # fire before Popen starts

    def _fake_run(*_args: object, **kwargs: Any) -> RunResult:
        captured["output"] = kwargs["output"]
        captured["output"].write_bytes(b"PARTIAL")
        raise PipelineError("cancelled by user")

    monkeypatch.setattr(sanitizer, "run_ffmpeg", _fake_run)

    with pytest.raises(PipelineError, match="cancelled by user"):
        sanitizer.sanitize_bitstream(src, out, cancel_token=token)
    assert not captured["output"].exists()
    assert not out.exists()


def test_sanitize_preserves_target_container_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "in.mkv"
    src.write_bytes(b"FAKE")
    out = tmp_path / "out.mkv"
    captured: dict[str, Path] = {}

    def _fake_run(cmd, *, output, **_kwargs):  # type: ignore[no-untyped-def]
        captured["output"] = output
        assert cmd.args[-1].endswith(".mkv")
        assert "-movflags" not in cmd.args
        output.write_bytes(b"FAKEOUT")
        return RunResult(returncode=0, duration_sec=1.0, output_path=output)

    monkeypatch.setattr(sanitizer, "run_ffmpeg", _fake_run)
    sanitizer.sanitize_bitstream(src, out)

    assert captured["output"].suffix == ".mkv"
    assert out.read_bytes() == b"FAKEOUT"
