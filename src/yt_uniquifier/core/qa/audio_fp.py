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
