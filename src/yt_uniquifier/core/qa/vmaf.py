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


# B5 (v0.6.0): auto-subsample target — 1 sample per ~0.25 s of source.
# At 24 fps, subsample=6 means one VMAF score per 6 frames ≈ 4 samples /
# second, which is well above VMAF's temporal smoothing window. Below
# this we waste CPU; above it we lose precision near scene cuts.
_AUTO_SUBSAMPLE_TARGET_INTERVAL_SEC = 0.25


def auto_subsample_for_duration(
    duration_sec: float,
    *,
    fps: float = 24.0,
    threshold_sec: float = 1800.0,
) -> int:
    """Return a sensible ``subsample`` for VMAF on a long source.

    B5 (v0.6.0): scoring every frame on a 4-hour 24 fps source is
    345k samples (4-11 h of compute). One sample per ~0.25 s
    converges within ~0.5 VMAF points of full-frame scoring on
    natural footage. For sources below ``threshold_sec`` we keep
    ``subsample=1`` so short calibration clips and snippet QA stay
    untouched.
    """
    if duration_sec < threshold_sec:
        return 1
    target = int(round(_AUTO_SUBSAMPLE_TARGET_INTERVAL_SEC * fps))
    return max(1, target)


def compute(
    input_path: Path,
    output_path: Path,
    *,
    threads: int = 4,
    subsample: int = 1,
    hdr_aware: bool = False,
) -> VMAFResult:
    """Run libvmaf comparing output (distorted) to input (reference).

    `subsample=N` tells libvmaf to score every N-th frame, cutting runtime
    by ~N× — useful for long files.

    `hdr_aware=True` switches to libvmaf's phone_model=0 (more lenient,
    perceptually-tuned for tonemapped HDR↔SDR pairs). Use for any pair where
    one side is HDR and the other is SDR; without this flag VMAF will look
    artificially low.
    """
    if not vmaf_available():
        return VMAFResult(
            available=False, score=None,
            note="ffmpeg has no libvmaf filter (build/install missing)",
        )
    libvmaf_args = f"libvmaf=n_threads={threads}"
    if subsample > 1:
        libvmaf_args += f":n_subsample={subsample}"
    if hdr_aware:
        libvmaf_args += ":phone_model=0"
    # Use the modern `scale=…:ref=…` form rather than the deprecated scale2ref
    # filter (LOW item from 2026-05-30 test report). The reference frame is
    # auto-resized to the distorted's dimensions; libvmaf needs them identical
    # and the encoder side may have shaved 2px via micro-crop.
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-nostats",
        "-i", str(output_path),
        "-i", str(input_path),
        "-lavfi",
        "[0:v]setsar=1[d];"
        "[1:v][0:v]scale=rw:rh:flags=lanczos[r0];"
        "[r0]setsar=1[r];"
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
    score = float(m.group(1))
    # libvmaf on very short clips occasionally returns ~0 even when frames
    # are essentially identical (HIGH-1 from 2026-05-30 test report). Treat
    # scores below 1.0 as unreliable and let the caller fall back to SSIM.
    if score < 1.0:
        return VMAFResult(
            available=True, score=None,
            note=(
                f"VMAF returned {score:.2f} (unreliable on this pair); "
                "falling back to SSIM"
            ),
        )
    return VMAFResult(available=True, score=score, note=None)
