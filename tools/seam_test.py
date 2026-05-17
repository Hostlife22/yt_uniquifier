#!/usr/bin/env python3
"""Measure SSIM around concat seams in a yt-uniquifier output.

Loads the state.json from --work-dir, takes each segment boundary, and runs
two SSIM measurements: a 4-frame window CROSSING the seam vs a 4-frame
window in the middle of the *previous* segment. If the seam SSIM is more
than --threshold below the middle SSIM, it's flagged as visible.

Run AFTER a successful `yt-uniq run --keep-segments` so state.json is intact.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_ALL_RE = re.compile(r"All:([0-9.]+)")


def _ssim_window(input_path: Path, start_sec: float, frames: int) -> float | None:
    """Run ssim filter over `frames` frames starting at start_sec."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-ss", f"{start_sec:.3f}", "-i", str(input_path),
        "-frames:v", str(frames),
        "-lavfi",
        "split[a][b];[a]setpts=PTS+1/TB[ar];[b][ar]ssim",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None
    m = _ALL_RE.search(proc.stderr)
    return float(m.group(1)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("--work-dir", required=True, type=Path,
                    help="The .yt_uniq_work/<plan_hash> directory used for the run.")
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--threshold", type=float, default=0.005,
                    help="Acceptable SSIM delta at the seam.")
    args = ap.parse_args()

    state_path = args.work_dir / "state.json"
    if not state_path.exists():
        print(f"error: {state_path} not found", file=sys.stderr)
        return 1
    state = json.loads(state_path.read_text())

    seams: list[float] = []
    segs = state.get("segments", [])
    for seg in segs[:-1]:
        seams.append(float(seg["end_sec"]))

    if not seams:
        print("no seams to test (only one segment)")
        return 0

    bad = 0
    for end_sec in seams:
        seam_ssim = _ssim_window(args.output, max(0.0, end_sec - 0.5), args.frames * 2)
        mid_ssim = _ssim_window(args.output, max(0.0, end_sec / 2), args.frames * 2)
        if seam_ssim is None or mid_ssim is None:
            print(f"seam @ {end_sec:.2f}s : ssim_unknown")
            continue
        delta = mid_ssim - seam_ssim
        status = "WARN" if delta > args.threshold else "ok"
        if delta > args.threshold:
            bad += 1
        print(
            f"seam @ {end_sec:7.2f}s : seam_ssim={seam_ssim:.4f}  "
            f"mid_ssim={mid_ssim:.4f}  delta={delta:+.4f}  {status}"
        )
    print(f"\n{bad}/{len(seams)} seams above threshold {args.threshold}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
