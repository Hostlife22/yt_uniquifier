"""Perceptual hash comparison.

Strategy: sample N frames at evenly-spaced timestamps from both files,
imagehash.phash each frame, then compute pairwise Hamming distance.

similarity = 1 - mean(distance) / 64  (phash is 8x8 = 64 bits)

Frames are extracted via ffmpeg into a PNG pipe scaled to 256px wide so
RAM stays bounded even on large source resolutions.
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin

# Per-process LRU cache for sampled frame sets. Both `report.build_report`
# and `cid_predict.predict` extract frames from the same (input, output)
# pair within one QA pass; without caching, each frame extraction is a
# ~3-5 s ffmpeg subprocess that gets paid twice per file. Keyed on
# (resolved_path, mtime_ns, size, n) so the cache auto-invalidates if
# the file is rewritten in place (size or mtime change). Lock guards the
# OrderedDict against parallel QA threads from the GUI.
_FRAME_CACHE_MAX = 8
_FRAME_CACHE: OrderedDict[tuple[str, int, int, int], list[Image.Image]] = OrderedDict()
_FRAME_CACHE_LOCK = threading.Lock()


def _frame_cache_key(path: Path, n: int) -> tuple[str, int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size, n)


def clear_frame_cache() -> None:
    """Drop all cached frame sets — exposed so tests can reset the cache."""
    with _FRAME_CACHE_LOCK:
        _FRAME_CACHE.clear()


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
    """Pull n frames evenly across the file as PIL images (256px wide).

    Caches the resulting frame list keyed on (resolved path, mtime_ns,
    size, n) so callers that ask for the same sample twice within a QA
    pass don't pay the ffmpeg subprocess cost twice.
    """
    cache_key = _frame_cache_key(path, n)
    if cache_key is not None:
        with _FRAME_CACHE_LOCK:
            hit = _FRAME_CACHE.get(cache_key)
            if hit is not None:
                _FRAME_CACHE.move_to_end(cache_key)
                return hit

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

    frames = _split_png_stream(proc.stdout)
    if cache_key is not None and frames:
        with _FRAME_CACHE_LOCK:
            _FRAME_CACHE[cache_key] = frames
            _FRAME_CACHE.move_to_end(cache_key)
            while len(_FRAME_CACHE) > _FRAME_CACHE_MAX:
                _FRAME_CACHE.popitem(last=False)
    return frames


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


def adaptive_n(duration_sec: float, floor: int = 60, ceiling: int = 600) -> int:
    """Pick a sample count proportional to duration (~30 frames per minute)."""
    if duration_sec <= 0:
        return floor
    return max(floor, min(ceiling, int(duration_sec / 60 * 30)))


def compare(input_path: Path, output_path: Path, n: int | None = 120) -> PHashStats:
    """Sample frames from both files and compute distance distribution.

    Pass n=None to auto-scale to duration via adaptive_n().
    """
    if n is None:
        n = adaptive_n(_probe_duration(input_path))
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


def sample_frames_range(
    path: Path, start_sec: float, span_sec: float, n: int,
) -> list[Image.Image]:
    """Sample n frames evenly from `[start_sec, start_sec + span_sec]`.

    Used by the v0.7 R4 live-divergence indicator: the encoded
    segment file (``seg_NNNN.mkv``) is a self-contained clip, but
    the source slice it corresponds to is a sub-range of the much
    larger source file. ``-ss start -t span`` input seek limits
    extraction to just that window so per-segment sampling stays
    O(span), not O(source duration).
    """
    if span_sec <= 0 or n <= 0:
        return []
    target_fps = max(n / span_sec, 0.01)
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", str(path),
        "-t", f"{span_sec:.3f}",
        "-vf", f"fps={target_fps:.6f},scale=256:-2",
        "-frames:v", str(n),
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"range frame extraction failed for {path}: "
            f"{exc.stderr.decode(errors='replace')[-300:]}",
        ) from exc
    return _split_png_stream(proc.stdout)


def compare_range_pair(
    source_path: Path, encoded_path: Path,
    *,
    source_start_sec: float, span_sec: float,
    n: int = 2,
) -> float | None:
    """Return mean pHash similarity between source[range] and encoded clip.

    `encoded_path` is a self-contained segment file produced by the
    pipeline (sampled across its full duration). `source_path` is
    the original source whose `[source_start_sec, +span_sec]` window
    maps to the encoded segment.

    Returns the similarity in `[0..1]` (1 = identical), or `None`
    when frame extraction returned 0 frames on either side — that
    signals a degenerate segment (zero duration / unreadable) and
    callers should drop it rather than treat it as "perfect match".
    """
    a = sample_frames_range(source_path, source_start_sec, span_sec, n)
    b = sample_frames(encoded_path, n)
    pairs = min(len(a), len(b))
    if pairs == 0:
        return None
    distances: list[int] = []
    for ia, ib in zip(a[:pairs], b[:pairs], strict=True):
        distances.append(int(imagehash.phash(ia) - imagehash.phash(ib)))
    dmean = sum(distances) / len(distances)
    return max(0.0, 1.0 - dmean / 64.0)


__all__ = [
    "PHashStats",
    "compare",
    "compare_range_pair",
    "sample_frames",
    "sample_frames_range",
]
