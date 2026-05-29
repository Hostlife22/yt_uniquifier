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
