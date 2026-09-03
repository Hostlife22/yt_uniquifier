#!/usr/bin/env python3
"""Compare source/output quality around known concat boundaries.

The legacy helper compared the output with a one-second-shifted copy of itself,
which mostly measured motion. This version compares decoded source and output at
the same timeline location, resets local timestamps, and performs a bounded
frame-offset search before comparing a seam window with an in-segment control.

Run after ``yt-uniq run --keep-segments`` so ``state.json`` is available.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

_ALL_RE = re.compile(r"All:([0-9.]+)")


@dataclass(frozen=True)
class RegisteredScore:
    score: float
    source_offset_frames: int


@dataclass(frozen=True)
class SeamWindow:
    boundary_sec: float
    seam_start_sec: float
    control_start_sec: float


def _probe_fps(path: Path) -> float:
    cmd = [
        ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "default=nw=1:nk=1",
        str(path),
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, timeout=30).strip()
        numerator, denominator = raw.split("/", 1)
        fps = float(numerator) / float(denominator)
    except (OSError, ValueError, ZeroDivisionError, subprocess.SubprocessError) as exc:
        raise ValueError(f"could not determine video FPS for {path}: {exc}") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"invalid video FPS for {path}: {fps!r}")
    return fps


def _ssim_pair(
    source_path: Path,
    output_path: Path,
    *,
    source_start_sec: float,
    output_start_sec: float,
    frames: int,
) -> float | None:
    """Measure SSIM for two independently seeked and timestamp-reset windows."""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostats",
        "-ss", f"{source_start_sec:.6f}", "-i", str(source_path),
        "-ss", f"{output_start_sec:.6f}", "-i", str(output_path),
        "-filter_complex",
        "[0:v][1:v]scale=rw:rh:flags=lanczos[ref0];"
        "[ref0]setsar=1,setpts=PTS-STARTPTS[ref];"
        "[1:v]setsar=1,setpts=PTS-STARTPTS[dist];"
        "[dist][ref]ssim[cmp]",
        "-map", "[cmp]", "-frames:v", str(frames), "-an", "-sn", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    matches = _ALL_RE.findall(proc.stderr)
    return float(matches[-1]) if matches else None


def _registered_ssim_window(
    source_path: Path,
    output_path: Path,
    *,
    start_sec: float,
    frames: int,
    fps: float,
    search_frames: int,
) -> RegisteredScore | None:
    """Return the best score across a bounded source-side frame-offset search."""
    best: RegisteredScore | None = None
    for offset_frames in range(-search_frames, search_frames + 1):
        source_start = start_sec + offset_frames / fps
        if source_start < 0:
            continue
        score = _ssim_pair(
            source_path,
            output_path,
            source_start_sec=source_start,
            output_start_sec=start_sec,
            frames=frames,
        )
        if score is None:
            continue
        candidate = RegisteredScore(score=score, source_offset_frames=offset_frames)
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _seam_windows(
    segments: object,
    *,
    frames: int,
    fps: float,
) -> list[SeamWindow]:
    if not isinstance(segments, list):
        raise ValueError("state.json 'segments' must be a list")
    window_sec = frames / fps
    windows: list[SeamWindow] = []
    for raw in segments[:-1]:
        if not isinstance(raw, dict):
            raise ValueError("state.json segment must be an object")
        try:
            start_sec = float(raw["start_sec"])
            end_sec = float(raw["end_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("state.json segment has invalid boundaries") from exc
        if not (math.isfinite(start_sec) and math.isfinite(end_sec)) or end_sec <= start_sec:
            raise ValueError("state.json segment boundaries must be finite and increasing")
        seam_start = max(0.0, end_sec - window_sec / 2)
        control_start = max(start_sec, (start_sec + end_sec - window_sec) / 2)
        windows.append(SeamWindow(end_sec, seam_start, control_start))
    return windows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="Final output from a yt-uniq run.")
    parser.add_argument("--source", required=True, type=Path, help="Original source file.")
    parser.add_argument(
        "--work-dir", required=True, type=Path,
        help="The .yt_uniq_work/<plan_hash> directory used for the run.",
    )
    parser.add_argument("--frames", type=int, default=8, help="Frames per comparison window.")
    parser.add_argument(
        "--search-frames", type=int, default=2,
        help="Maximum source-side registration offset in either direction.",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.005,
        help="Acceptable registered SSIM drop at the seam.",
    )
    args = parser.parse_args()

    if args.frames < 2 or args.search_frames < 0 or args.threshold < 0:
        parser.error("frames must be >= 2; search-frames and threshold must be >= 0")
    for label, path in (("source", args.source), ("output", args.output)):
        if not path.is_file():
            print(f"error: {label} file not found: {path}", file=sys.stderr)
            return 1

    state_path = args.work_dir / "state.json"
    if not state_path.is_file():
        print(f"error: {state_path} not found", file=sys.stderr)
        return 1
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        fps = _probe_fps(args.output)
        windows = _seam_windows(state.get("segments"), frames=args.frames, fps=fps)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: cannot prepare seam analysis: {exc}", file=sys.stderr)
        return 1

    if not windows:
        print("no seams to test (only one segment)")
        return 0

    bad = 0
    for window in windows:
        seam = _registered_ssim_window(
            args.source,
            args.output,
            start_sec=window.seam_start_sec,
            frames=args.frames,
            fps=fps,
            search_frames=args.search_frames,
        )
        control = _registered_ssim_window(
            args.source,
            args.output,
            start_sec=window.control_start_sec,
            frames=args.frames,
            fps=fps,
            search_frames=args.search_frames,
        )
        if seam is None or control is None:
            bad += 1
            print(f"seam @ {window.boundary_sec:.2f}s : ERROR metric unavailable")
            continue
        delta = control.score - seam.score
        failed = delta > args.threshold
        bad += int(failed)
        status = "WARN" if failed else "ok"
        print(
            f"seam @ {window.boundary_sec:7.2f}s : seam_ssim={seam.score:.4f} "
            f"(offset={seam.source_offset_frames:+d}f)  control_ssim={control.score:.4f} "
            f"(offset={control.source_offset_frames:+d}f)  delta={delta:+.4f}  {status}"
        )
    print(f"\n{bad}/{len(windows)} seams failed threshold {args.threshold}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
