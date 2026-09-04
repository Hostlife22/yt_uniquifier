"""SSIM via ffmpeg's `ssim` filter."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

if TYPE_CHECKING:
    from yt_uniquifier.core.runner import CancelToken


@dataclass(frozen=True)
class SSIMResult:
    score: float | None
    note: str | None = None


# ffmpeg emits: '[Parsed_ssim_0 @ 0x...] SSIM Y:0.9912 U:0.9933 V:0.9931 All:0.9923 (21.13dB)'
_ALL_RE = re.compile(r"All:([0-9.]+)")


def compute(
    input_path: Path,
    output_path: Path,
    *,
    reset_pts: bool = False,
    cancel_token: CancelToken | None = None,
) -> SSIMResult:
    # scale2ref rescales the reference to the distorted's dimensions, so SSIM
    # doesn't refuse on 2px micro-crop differences. setsar=1 to drop SAR drift.
    graph = (
        "[1:v]setpts=PTS-STARTPTS[ref0];"
        "[0:v]setpts=PTS-STARTPTS[dist0];"
        "[ref0][dist0]scale2ref=w=iw:h=ih[ref][dist];"
        "[dist]setsar=1[d];[ref]setsar=1[r];[d][r]ssim"
        if reset_pts
        else (
            "[1:v][0:v]scale2ref=w=iw:h=ih[ref][dist];"
            "[dist]setsar=1[d];[ref]setsar=1[r];[d][r]ssim"
        )
    )
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-nostats",
        "-i", str(output_path),
        "-i", str(input_path),
        "-lavfi",
        graph,
        "-f", "null",
        "-",
    ]
    metric_log: str
    if cancel_token is None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            return SSIMResult(score=None, note="ssim timed out")
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()[-1] if proc.stderr else "unknown"
            return SSIMResult(score=None, note=f"ssim failed: {tail}")
        metric_log = proc.stderr
    else:
        from yt_uniquifier.core.pipeline import BuiltCommand
        from yt_uniquifier.core.runner import run as run_ffmpeg

        with tempfile.TemporaryDirectory(prefix="ssim_registered_") as tmp:
            log_path = Path(tmp) / "ssim.log"
            try:
                run_ffmpeg(
                    BuiltCommand(args=cmd),
                    output=Path("-"),
                    cancel_token=cancel_token,
                    log_path=log_path,
                    progress_via_stdout=False,
                    wall_timeout_sec=3600.0,
                )
            except PipelineError as exc:
                if cancel_token.is_cancelled():
                    raise
                return SSIMResult(score=None, note=f"ssim failed: {exc}")
            metric_log = log_path.read_text(encoding="utf-8", errors="replace")
    m = _ALL_RE.search(metric_log)
    if not m:
        return SSIMResult(score=None, note="ssim score not found in output")
    return SSIMResult(score=float(m.group(1)))
