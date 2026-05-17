"""VMAF score via ffmpeg's libvmaf filter.

If the local ffmpeg build doesn't have libvmaf, this module returns
available=False and the QA report records a 'skipped' note. No crash.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin


@dataclass(frozen=True)
class VMAFResult:
    available: bool
    score: float | None
    note: str | None


@lru_cache(maxsize=1)
def vmaf_available() -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return "libvmaf" in proc.stdout


_SCORE_RE = re.compile(r"VMAF score:\s*([0-9.]+)")


def compute(
    input_path: Path,
    output_path: Path,
    *,
    threads: int = 4,
    subsample: int = 1,
) -> VMAFResult:
    """Run libvmaf comparing output (distorted) to input (reference).

    `subsample=N` tells libvmaf to score every N-th frame, cutting runtime
    by ~N× — useful for long files. The score is the harmonic mean over the
    sampled frames so it's still comparable across runs.
    """
    if not vmaf_available():
        return VMAFResult(
            available=False, score=None,
            note="ffmpeg has no libvmaf filter (build/install missing)",
        )
    libvmaf_args = f"libvmaf=n_threads={threads}"
    if subsample > 1:
        libvmaf_args += f":n_subsample={subsample}"
    # scale2ref the reference to match the distorted's dims; libvmaf needs them
    # identical and the encoder side may have shaved 2px via micro-crop.
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-nostats",
        "-i", str(output_path),
        "-i", str(input_path),
        "-lavfi",
        "[1:v][0:v]scale2ref=w=iw:h=ih[ref][dist];"
        "[dist]setsar=1[d];[ref]setsar=1[r];"
        f"[d][r]{libvmaf_args}",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return VMAFResult(available=True, score=None, note="vmaf timed out")
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-1] if proc.stderr else "unknown"
        return VMAFResult(available=True, score=None, note=f"vmaf failed: {tail}")
    m = _SCORE_RE.search(proc.stderr)
    if not m:
        return VMAFResult(available=True, score=None, note="vmaf score not found in output")
    return VMAFResult(available=True, score=float(m.group(1)), note=None)
