"""Audio fingerprint similarity via `fpcalc` (chromaprint).

If `fpcalc` is not installed, this module degrades gracefully (returns
available=False) instead of crashing the QA pipeline.

The fingerprint is a base64-encoded list of 32-bit subfingerprints. We
compare via Jaccard overlap of the two sets — robust to small offsets.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .utils import _decode_chromaprint  # noqa: TID252


@dataclass(frozen=True)
class AudioFPResult:
    available: bool
    similarity: float | None
    note: str | None


@dataclass(frozen=True)
class AudioFPHamming:
    """Bit-level Hamming distance between paired chromaprint subfingerprints.

    Each subfingerprint is a 32-bit integer. Pair frame i of input with
    frame i of output, popcount(a XOR b), average over all paired frames.

    Interpretation (chromaprint heuristic):
      ≤ 5 bits/frame → high-confidence match
      6 – 14         → match
      15 – 25        → uncertain
      ≥ 26           → no match
      ≥ 30           → high-confidence non-match
    """

    available: bool
    hamming_per_frame: float | None
    match_confidence: float | None
    note: str | None = None


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def _run_fpcalc(path: Path) -> dict[str, object] | None:
    if not fpcalc_available():
        return None
    cmd = ["fpcalc", "-json", "-length", "600", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def compare(input_path: Path, output_path: Path) -> AudioFPResult:
    if not fpcalc_available():
        return AudioFPResult(
            available=False,
            similarity=None,
            note="fpcalc not in PATH (install chromaprint to enable)",
        )
    a = _run_fpcalc(input_path)
    b = _run_fpcalc(output_path)
    if a is None or b is None:
        return AudioFPResult(
            available=False,
            similarity=None,
            note="fpcalc invocation failed for one of the inputs",
        )
    try:
        ai = _decode_chromaprint(str(a["fingerprint"]))
        bi = _decode_chromaprint(str(b["fingerprint"]))
    except (KeyError, ValueError):
        return AudioFPResult(
            available=False, similarity=None, note="malformed fpcalc output"
        )
    if not ai or not bi:
        return AudioFPResult(available=True, similarity=0.0, note=None)

    set_a, set_b = set(ai), set(bi)
    union = len(set_a | set_b)
    if union == 0:
        return AudioFPResult(available=True, similarity=0.0, note=None)
    jaccard = len(set_a & set_b) / union
    return AudioFPResult(available=True, similarity=jaccard, note=None)


def _hamming_per_frame(a: list[int], b: list[int]) -> float:
    """Mean bits-different per paired 32-bit subfingerprint over min length."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    total = 0
    for ai, bi in zip(a[:n], b[:n], strict=True):
        total += int(ai ^ bi).bit_count()
    return total / n


@dataclass(frozen=True)
class AudioFPVariance:
    """Per-window Hamming distance variance — KPI for divergent audio.

    `hamming_per_window[i]` = mean Hamming distance for the chromaprint
    subfingerprints inside window i (paired input vs output frames).
    `variance_between_windows` = stdev of those means.

    With v0.3.3-style uniform audio: variance ≈ 0 (all windows have the
    same params, so per-window deltas are similar). With v0.4.2 windowed
    audio: variance ≥ 4 bits expected on real fixtures.
    """

    available: bool
    hamming_per_window: list[float] | None
    variance_between_windows: float | None
    note: str | None = None


def compare_hamming_per_window(
    input_path: Path, output_path: Path, n_windows: int = 5,
) -> AudioFPVariance:
    """Split paired chromaprint streams into n_windows equal chunks.

    For each chunk, mean Hamming distance over its frames. Returns the
    stdev across chunks. With windowed audio that varies across the
    timeline, stdev grows; with uniform audio it stays near 0.
    """
    if not fpcalc_available():
        return AudioFPVariance(
            available=False, hamming_per_window=None,
            variance_between_windows=None,
            note="fpcalc not in PATH (install chromaprint to enable)",
        )
    a = _run_fpcalc(input_path)
    b = _run_fpcalc(output_path)
    if a is None or b is None:
        return AudioFPVariance(
            available=False, hamming_per_window=None,
            variance_between_windows=None,
            note="fpcalc invocation failed for one of the inputs",
        )
    try:
        ai = _decode_chromaprint(str(a["fingerprint"]))
        bi = _decode_chromaprint(str(b["fingerprint"]))
    except (KeyError, ValueError):
        return AudioFPVariance(
            available=False, hamming_per_window=None,
            variance_between_windows=None,
            note="malformed fpcalc output",
        )
    n = min(len(ai), len(bi))
    if n < n_windows or n_windows < 2:
        return AudioFPVariance(
            available=True, hamming_per_window=[0.0],
            variance_between_windows=0.0,
        )
    window_size = n // n_windows
    means: list[float] = []
    for w in range(n_windows):
        start = w * window_size
        end = (w + 1) * window_size if w < n_windows - 1 else n
        chunk_a = ai[start:end]
        chunk_b = bi[start:end]
        means.append(_hamming_per_frame(chunk_a, chunk_b))
    # Population stdev — simple measure of spread.
    mean_of_means = sum(means) / len(means)
    variance = (
        sum((m - mean_of_means) ** 2 for m in means) / len(means)
    ) ** 0.5
    return AudioFPVariance(
        available=True,
        hamming_per_window=means,
        variance_between_windows=variance,
    )


def compare_hamming(input_path: Path, output_path: Path) -> AudioFPHamming:
    """Bit-level Hamming distance over paired chromaprint subfingerprints.

    Returns AudioFPHamming with available=False if fpcalc is missing.
    `match_confidence` is `1 - mean_hamming / 32`, in [0, 1]: higher means
    closer to the input fingerprint, i.e. *worse* for CID divergence.
    """
    if not fpcalc_available():
        return AudioFPHamming(
            available=False,
            hamming_per_frame=None,
            match_confidence=None,
            note="fpcalc not in PATH (install chromaprint to enable)",
        )
    a = _run_fpcalc(input_path)
    b = _run_fpcalc(output_path)
    if a is None or b is None:
        return AudioFPHamming(
            available=False,
            hamming_per_frame=None,
            match_confidence=None,
            note="fpcalc invocation failed for one of the inputs",
        )
    try:
        ai = _decode_chromaprint(str(a["fingerprint"]))
        bi = _decode_chromaprint(str(b["fingerprint"]))
    except (KeyError, ValueError):
        return AudioFPHamming(
            available=False,
            hamming_per_frame=None,
            match_confidence=None,
            note="malformed fpcalc output",
        )
    if not ai or not bi:
        return AudioFPHamming(
            available=True, hamming_per_frame=0.0, match_confidence=1.0, note=None,
        )
    mean = _hamming_per_frame(ai, bi)
    return AudioFPHamming(
        available=True,
        hamming_per_frame=mean,
        match_confidence=max(0.0, min(1.0, 1.0 - mean / 32.0)),
        note=None,
    )
