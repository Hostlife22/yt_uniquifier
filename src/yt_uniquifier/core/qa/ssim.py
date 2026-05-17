"""SSIM via ffmpeg's `ssim` filter."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin


@dataclass(frozen=True)
class SSIMResult:
    score: float | None
    note: str | None = None


# ffmpeg emits: '[Parsed_ssim_0 @ 0x...] SSIM Y:0.9912 U:0.9933 V:0.9931 All:0.9923 (21.13dB)'
_ALL_RE = re.compile(r"All:([0-9.]+)")


def compute(input_path: Path, output_path: Path) -> SSIMResult:
    # scale2ref rescales the reference to the distorted's dimensions, so SSIM
    # doesn't refuse on 2px micro-crop differences. setsar=1 to drop SAR drift.
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-nostats",
        "-i", str(output_path),
        "-i", str(input_path),
        "-lavfi",
        "[1:v][0:v]scale2ref=w=iw:h=ih[ref][dist];"
        "[dist]setsar=1[d];[ref]setsar=1[r];[d][r]ssim",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return SSIMResult(score=None, note="ssim timed out")
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-1] if proc.stderr else "unknown"
        return SSIMResult(score=None, note=f"ssim failed: {tail}")
    m = _ALL_RE.search(proc.stderr)
    if not m:
        return SSIMResult(score=None, note="ssim score not found in output")
    return SSIMResult(score=float(m.group(1)))
