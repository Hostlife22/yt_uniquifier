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

from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

from .utils import _decode_chromaprint  # noqa: TID252

_MAX_FINGERPRINT_SEC = 600.0
_STRATIFIED_WINDOWS = 5


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


def _stratified_windows(
    duration_sec: float,
    *,
    total_sec: float = _MAX_FINGERPRINT_SEC,
    count: int = _STRATIFIED_WINDOWS,
) -> list[tuple[float, float]]:
    """Return evenly-spaced windows spanning start, middle and tail."""
    if duration_sec <= 0:
        return []
    if duration_sec <= total_sec or count <= 1:
        return [(0.0, duration_sec)]
    window_sec = total_sec / count
    last_start = duration_sec - window_sec
    return [
        (last_start * index / (count - 1), window_sec)
        for index in range(count)
    ]


def _run_fpcalc_stratified(
    path: Path,
    duration_sec: float,
) -> dict[str, object] | None:
    """Fingerprint one concatenated PCM stream sampled across a long file."""
    windows = _stratified_windows(duration_sec)
    if len(windows) <= 1:
        return _run_fpcalc(path)
    chains = [
        f"[0:a:0]atrim=start={start:.6f}:duration={length:.6f},"
        f"asetpts=PTS-STARTPTS[a{index}]"
        for index, (start, length) in enumerate(windows)
    ]
    inputs = "".join(f"[a{index}]" for index in range(len(windows)))
    graph = ";".join([*chains, f"{inputs}concat=n={len(windows)}:v=0:a=1[out]"])
    ffmpeg_cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-v", "error",
        "-i", str(path),
        "-filter_complex", graph,
        "-map", "[out]",
        "-ac", "2",
        "-ar", "11025",
        "-f", "s16le",
        "pipe:1",
    ]
    fpcalc_cmd = [
        shutil.which("fpcalc") or "fpcalc",
        "-format", "s16le",
        "-rate", "11025",
        "-channels", "2",
        "-json",
        "-length", str(int(_MAX_FINGERPRINT_SEC)),
        "-",
    ]
    ffmpeg_proc: subprocess.Popen[bytes] | None = None
    try:
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            # Error details are intentionally collapsed into the caller's
            # availability note.  A separate unread PIPE can deadlock if a
            # damaged long input emits enough decoder diagnostics.
            stderr=subprocess.DEVNULL,
        )
        if ffmpeg_proc.stdout is None:
            ffmpeg_proc.kill()
            ffmpeg_proc.wait()
            return None
        fpcalc_proc = subprocess.run(
            fpcalc_cmd,
            stdin=ffmpeg_proc.stdout,
            capture_output=True,
            timeout=180,
            check=False,
        )
        ffmpeg_proc.stdout.close()
        ffmpeg_rc = ffmpeg_proc.wait(timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
            ffmpeg_proc.kill()
            ffmpeg_proc.wait()
        return None
    if ffmpeg_rc != 0 or fpcalc_proc.returncode != 0:
        return None
    try:
        parsed = json.loads(fpcalc_proc.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def compare(input_path: Path, output_path: Path) -> AudioFPResult:
    return analyze_pair(input_path, output_path).similarity


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


@dataclass(frozen=True)
class AudioFPPairAnalysis:
    """All legacy fingerprint diagnostics derived from one extraction per file."""

    similarity: AudioFPResult
    hamming: AudioFPHamming
    variance: AudioFPVariance
    coverage_note: str | None = None


def _unavailable_pair(note: str) -> AudioFPPairAnalysis:
    return AudioFPPairAnalysis(
        similarity=AudioFPResult(False, None, note),
        hamming=AudioFPHamming(False, None, None, note),
        variance=AudioFPVariance(False, None, None, note),
    )


def analyze_pair(
    input_path: Path,
    output_path: Path,
    *,
    n_windows: int = 5,
    input_duration_sec: float | None = None,
    output_duration_sec: float | None = None,
) -> AudioFPPairAnalysis:
    """Extract two fingerprints once, then derive every diagnostic in memory."""
    if not fpcalc_available():
        return _unavailable_pair("fpcalc not in PATH (install chromaprint to enable)")
    input_is_long = (
        input_duration_sec is not None and input_duration_sec > _MAX_FINGERPRINT_SEC
    )
    output_is_long = (
        output_duration_sec is not None and output_duration_sec > _MAX_FINGERPRINT_SEC
    )
    a = (
        _run_fpcalc_stratified(input_path, input_duration_sec)
        if input_is_long and input_duration_sec is not None
        else _run_fpcalc(input_path)
    )
    b = (
        _run_fpcalc_stratified(output_path, output_duration_sec)
        if output_is_long and output_duration_sec is not None
        else _run_fpcalc(output_path)
    )
    if a is None or b is None:
        return _unavailable_pair("fpcalc invocation failed for one of the inputs")
    try:
        ai = _decode_chromaprint(str(a["fingerprint"]))
        bi = _decode_chromaprint(str(b["fingerprint"]))
    except (KeyError, ValueError):
        return _unavailable_pair("malformed fpcalc output")
    if not ai or not bi:
        return _unavailable_pair(
            "fpcalc returned an empty fingerprint (silent / unsupported audio)"
        )

    set_a, set_b = set(ai), set(bi)
    union = len(set_a | set_b)
    similarity = len(set_a & set_b) / union if union else 0.0
    mean_hamming = _hamming_per_frame(ai, bi)
    hamming = AudioFPHamming(
        available=True,
        hamming_per_frame=mean_hamming,
        match_confidence=max(0.0, min(1.0, 1.0 - mean_hamming / 32.0)),
    )

    n = min(len(ai), len(bi))
    if n < n_windows or n_windows < 2:
        means = [mean_hamming]
    else:
        window_size = n // n_windows
        means = []
        for window in range(n_windows):
            start = window * window_size
            end = (window + 1) * window_size if window < n_windows - 1 else n
            means.append(_hamming_per_frame(ai[start:end], bi[start:end]))
    mean_of_means = sum(means) / len(means)
    variance = (
        sum((value - mean_of_means) ** 2 for value in means) / len(means)
    ) ** 0.5
    return AudioFPPairAnalysis(
        similarity=AudioFPResult(True, similarity, None),
        hamming=hamming,
        variance=AudioFPVariance(True, means, variance),
        coverage_note=(
            "stratified 600-second fingerprint coverage across the full timeline"
            if input_is_long or output_is_long
            else None
        ),
    )


def compare_hamming_per_window(
    input_path: Path, output_path: Path, n_windows: int = 5,
) -> AudioFPVariance:
    """Split paired chromaprint streams into n_windows equal chunks.

    For each chunk, mean Hamming distance over its frames. Returns the
    stdev across chunks. With windowed audio that varies across the
    timeline, stdev grows; with uniform audio it stays near 0.
    """
    return analyze_pair(
        input_path,
        output_path,
        n_windows=n_windows,
    ).variance


def compare_hamming(input_path: Path, output_path: Path) -> AudioFPHamming:
    """Bit-level Hamming distance over paired chromaprint subfingerprints.

    Returns AudioFPHamming with available=False if fpcalc is missing.
    `match_confidence` is `1 - mean_hamming / 32`, in [0, 1]: higher means
    closer to the input fingerprint, i.e. *worse* for CID divergence.
    """
    return analyze_pair(input_path, output_path).hamming
