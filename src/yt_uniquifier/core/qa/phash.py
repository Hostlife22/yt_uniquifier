"""Perceptual hash comparison.

Strategy: sample N frames at evenly-spaced timestamps from both files,
imagehash.phash each frame, then compute pairwise Hamming distance.

similarity = 1 - mean(distance) / 64  (phash is 8x8 = 64 bits)

Frames are extracted via ffmpeg into a PNG pipe scaled to 256px wide so
RAM stays bounded even on large source resolutions.
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin


@dataclass(frozen=True)
class PHashStats:
    samples: int
    distance_min: int
    distance_mean: float
    distance_max: int
    similarity: float  # 0..1; 1 = identical


def _probe_duration(path: Path) -> float:
    """Quick duration probe via ffprobe -of csv (light vs full SourceMeta probe)."""
    cmd = [
        ffprobe_bin(),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(f"duration probe failed for {path}: {exc.stderr}") from exc
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise PipelineError(f"unparseable duration for {path}") from exc


def sample_frames(path: Path, n: int = 120) -> list[Image.Image]:
    """Pull n frames evenly across the file as PIL images (256px wide)."""
    duration = _probe_duration(path)
    if duration <= 0:
        return []
    # Pull frames at uniform timestamps via `select='eq(n,N1)+eq(n,N2)+...'` is
    # awkward; simpler is to use the `fps` filter at the right rate then cap
    # frame count.
    target_fps = max(n / duration, 0.01)
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path),
        "-vf", f"fps={target_fps:.6f},scale=256:-2",
        "-frames:v", str(n),
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600, check=True)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"frame extraction failed for {path}: {exc.stderr.decode(errors='replace')[-300:]}"
        ) from exc

    return _split_png_stream(proc.stdout)


def _split_png_stream(blob: bytes) -> list[Image.Image]:
    """Split a concatenated PNG stream into individual images."""
    sig = b"\x89PNG\r\n\x1a\n"
    out: list[Image.Image] = []
    i = 0
    n = len(blob)
    while True:
        start = blob.find(sig, i)
        if start < 0:
            break
        nxt = blob.find(sig, start + len(sig))
        end = nxt if nxt >= 0 else n
        out.append(Image.open(io.BytesIO(blob[start:end])).copy())
        if nxt < 0:
            break
        i = nxt
    return out


def compare(input_path: Path, output_path: Path, n: int = 120) -> PHashStats:
    a = sample_frames(input_path, n)
    b = sample_frames(output_path, n)
    pairs = min(len(a), len(b))
    if pairs == 0:
        return PHashStats(samples=0, distance_min=0, distance_mean=0.0,
                          distance_max=0, similarity=0.0)

    distances: list[int] = []
    for ia, ib in zip(a[:pairs], b[:pairs], strict=True):
        ha = imagehash.phash(ia)
        hb = imagehash.phash(ib)
        distances.append(int(ha - hb))

    dmin = min(distances)
    dmax = max(distances)
    dmean = sum(distances) / len(distances)
    similarity = max(0.0, 1.0 - dmean / 64.0)
    return PHashStats(
        samples=pairs,
        distance_min=dmin,
        distance_mean=dmean,
        distance_max=dmax,
        similarity=similarity,
    )


__all__ = ["PHashStats", "compare", "sample_frames"]
