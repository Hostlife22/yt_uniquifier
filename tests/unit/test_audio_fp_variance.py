"""v0.4.2 per-window Hamming variance KPI."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

from yt_uniquifier.core.qa.audio_fp import (
    compare_hamming_per_window,
)


def _make_fp(ints: list[int]) -> str:
    """Build a base64 chromaprint-style fingerprint from int list."""
    header = bytes([0x01, 0x02, 0x03, 0x04])
    body = b"".join(i.to_bytes(4, "big", signed=False) for i in ints)
    return base64.urlsafe_b64encode(header + body).decode().rstrip("=")


def test_uniform_audio_zero_variance(tmp_path: Path) -> None:
    """Identical fingerprint pair (uniform audio) → variance = 0."""
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    fp = _make_fp(list(range(100)))
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=True), patch(
        "yt_uniquifier.core.qa.audio_fp._run_fpcalc",
        side_effect=[{"fingerprint": fp}, {"fingerprint": fp}],
    ):
        result = compare_hamming_per_window(in_p, out_p, n_windows=5)
    assert result.available is True
    assert result.variance_between_windows == 0.0
    assert all(v == 0.0 for v in result.hamming_per_window)


def test_divergent_audio_has_nonzero_variance(tmp_path: Path) -> None:
    """Output with different per-window mutations → variance > 0."""
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    # Input: all zeros. Output: window 0 all-ones (32 bit diff per frame),
    # window 1 all-zeros (0 diff), window 2 half-bits, etc.
    n_per_window = 20
    n_windows = 5
    input_ints = [0] * (n_per_window * n_windows)
    output_ints: list[int] = []
    for w in range(n_windows):
        # Per-window XOR pattern increasing the bits-set count.
        bits = w * 6  # 0, 6, 12, 18, 24 → varies per window
        xor_val = (1 << bits) - 1 if bits > 0 else 0
        output_ints.extend([xor_val] * n_per_window)
    in_fp = _make_fp(input_ints)
    out_fp = _make_fp(output_ints)
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=True), patch(
        "yt_uniquifier.core.qa.audio_fp._run_fpcalc",
        side_effect=[{"fingerprint": in_fp}, {"fingerprint": out_fp}],
    ):
        result = compare_hamming_per_window(in_p, out_p, n_windows=n_windows)
    assert result.available is True
    assert result.variance_between_windows > 4.0  # well above the KPI floor


def test_unavailable_when_fpcalc_missing(tmp_path: Path) -> None:
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=False):
        result = compare_hamming_per_window(in_p, out_p)
    assert result.available is False
    assert result.variance_between_windows is None
    assert result.note and "fpcalc" in result.note


def test_too_few_frames_for_windowing(tmp_path: Path) -> None:
    """Less frames than windows requested → single-window degenerate result."""
    in_p = tmp_path / "a.wav"
    in_p.touch()
    out_p = tmp_path / "b.wav"
    out_p.touch()
    short_fp = _make_fp([0, 1, 2])  # only 3 frames
    with patch("yt_uniquifier.core.qa.audio_fp.fpcalc_available", return_value=True), patch(
        "yt_uniquifier.core.qa.audio_fp._run_fpcalc",
        side_effect=[{"fingerprint": short_fp}, {"fingerprint": short_fp}],
    ):
        result = compare_hamming_per_window(in_p, out_p, n_windows=5)
    assert result.available is True
    assert result.variance_between_windows == 0.0
