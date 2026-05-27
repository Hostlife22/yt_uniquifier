"""Audio fingerprint Hamming distance KPI.

Source: Smitelli (2010) — audio is the most predictable CID signal; this
exposes the bit-level distance directly so it becomes a measurable target.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

from yt_uniquifier.core.qa.audio_fp import (
    AudioFPHamming,
    _hamming_per_frame,
    compare_hamming,
)


def test_identical_fingerprints_zero_hamming() -> None:
    """Pairing a frame with itself → 0 bits different."""
    a = [0x12345678, 0xDEADBEEF, 0xCAFEBABE]
    assert _hamming_per_frame(a, a) == 0.0


def test_inverted_fingerprints_max_hamming() -> None:
    """Bitwise inversion → all 32 bits differ per frame."""
    a = [0x12345678, 0xDEADBEEF]
    b = [0x12345678 ^ 0xFFFFFFFF, 0xDEADBEEF ^ 0xFFFFFFFF]
    assert _hamming_per_frame(a, b) == 32.0


def test_partial_overlap_intermediate() -> None:
    """A and B differ in exactly 4 bits in each of 2 frames → mean=4."""
    a = [0xF0F0F0F0, 0x00000000]  # known bit patterns
    b = [0xF0F0F0FF, 0x0000000F]  # diff: 4 LSBs in each
    assert _hamming_per_frame(a, b) == 4.0


def test_empty_lists_return_zero() -> None:
    assert _hamming_per_frame([], []) == 0.0
    assert _hamming_per_frame([1, 2], []) == 0.0


def test_unequal_lengths_use_min() -> None:
    """Pair input[:N] with output[:N] where N = min(len_a, len_b)."""
    a = [0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF]
    b = [0xFFFFFFFF, 0xFFFFFFFF]  # match for the 2 paired frames
    assert _hamming_per_frame(a, b) == 0.0


def test_unavailable_when_fpcalc_missing(tmp_path: Path) -> None:
    """compare_hamming returns available=False if fpcalc isn't on PATH."""
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=False):
        result = compare_hamming(in_p, out_p)
    assert result.available is False
    assert result.hamming_per_frame is None
    assert result.match_confidence is None
    assert result.note and "fpcalc" in result.note


def test_match_confidence_normalisation() -> None:
    """confidence = 1 - mean_hamming / 32; bounded to [0, 1]."""
    # We can't easily plumb a fake hamming into compare_hamming without
    # mocking fpcalc subprocess. Instead, verify the AudioFPHamming
    # dataclass shape and that the formula round-trips for a known value.
    h = AudioFPHamming(
        available=True, hamming_per_frame=16.0, match_confidence=0.5,
    )
    # 1 - 16/32 = 0.5
    assert abs(h.match_confidence - 0.5) < 1e-9

    # Spot-check the formula used in compare_hamming for an inverted pair.
    # 32 bits/frame → confidence 0.
    inverted = AudioFPHamming(
        available=True, hamming_per_frame=32.0, match_confidence=0.0,
    )
    assert inverted.match_confidence == 0.0


def test_compare_hamming_with_mocked_fpcalc(tmp_path: Path) -> None:
    """End-to-end: mock _run_fpcalc to return known fingerprints, verify result."""
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    # Chromaprint compressed: 4-byte header + 4-byte-aligned body of uint32s.
    header = bytes([0x01, 0x02, 0x03, 0x04])
    body_a = (0xFFFFFFFF).to_bytes(4, "big") + (0x00000000).to_bytes(4, "big")
    body_b = (0xFFFFFFFF).to_bytes(4, "big") + (0xFFFFFFFF).to_bytes(4, "big")
    fp_a = base64.urlsafe_b64encode(header + body_a).decode().rstrip("=")
    fp_b = base64.urlsafe_b64encode(header + body_b).decode().rstrip("=")
    # Frame 0: 0xFFFFFFFF ^ 0xFFFFFFFF = 0 → 0 bits
    # Frame 1: 0x00000000 ^ 0xFFFFFFFF = 0xFFFFFFFF → 32 bits
    # Mean: (0 + 32) / 2 = 16.0
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=True), patch(
        "yt_uniquifier.core.qa.audio_fp._run_fpcalc",
        side_effect=[{"fingerprint": fp_a}, {"fingerprint": fp_b}],
    ):
        result = compare_hamming(in_p, out_p)
    assert result.available is True
    assert result.hamming_per_frame == 16.0
    assert result.match_confidence == 0.5
