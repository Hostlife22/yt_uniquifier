"""Detect which ffmpeg encoders actually work on this machine.

Strategy: enumerate a fixed candidate list (NVENC, QSV, VideoToolbox, AMF, x264/x265)
and run a short null-output encode for each. Cache the result keyed by ffmpeg --version,
so repeated `yt-uniq` invocations don't re-probe.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from yt_uniquifier.core.errors import EncoderError
from yt_uniquifier.core.models import EncoderCandidate, EncoderKind, EncoderVendor
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

CACHE_PATH = Path.home() / ".cache" / "yt_uniquifier" / "encoders.json"
CACHE_TTL_SEC = 7 * 24 * 3600

# Ordered by preference: hardware first, software fallback last.
_CANDIDATES: tuple[tuple[str, EncoderVendor, EncoderKind], ...] = (
    ("h264_nvenc", "nvenc", "h264"),
    ("hevc_nvenc", "nvenc", "hevc"),
    ("h264_qsv", "qsv", "h264"),
    ("hevc_qsv", "qsv", "hevc"),
    ("h264_videotoolbox", "videotoolbox", "h264"),
    ("hevc_videotoolbox", "videotoolbox", "hevc"),
    ("h264_amf", "amf", "h264"),
    ("hevc_amf", "amf", "hevc"),
    ("libx264", "x264", "h264"),
    ("libx265", "x265", "hevc"),
)


def detect_encoders(force: bool = False) -> list[EncoderCandidate]:
    """Return list of encoder candidates with works=True/False.

    Cached at CACHE_PATH keyed by sha256 of `ffmpeg -version` output.
    Pass force=True to bypass the cache and re-probe.
    """
    version_key = _ffmpeg_version_hash()
    if not force:
        cached = _load_cache(version_key)
        if cached is not None:
            return cached

    results = [_probe_one(name, vendor, codec) for name, vendor, codec in _CANDIDATES]
    _save_cache(version_key, results)
    return results


def pick_encoder(
    candidates: Sequence[EncoderCandidate],
    *,
    prefer: Sequence[str] | None = None,
    codec: EncoderKind = "h264",
) -> EncoderCandidate:
    """Pick highest-priority working encoder matching codec.

    Order: explicit `prefer` list first (in order), then the natural candidate
    order. libx264/libx265 always available as fallback.
    """
    working = [c for c in candidates if c.works and c.codec == codec]
    if not working:
        raise EncoderError(
            f"no working encoder for codec={codec!r}. "
            "Check that ffmpeg is installed with libx264/libx265 support."
        )

    if prefer:
        by_name = {c.name: c for c in working}
        for name in prefer:
            if name in by_name:
                return by_name[name]

    # Otherwise pick first working candidate in canonical order.
    canonical_order = [name for name, _, k in _CANDIDATES if k == codec]
    by_name = {c.name: c for c in working}
    for name in canonical_order:
        if name in by_name:
            return by_name[name]

    return working[0]  # unreachable in practice


def _probe_one(name: str, vendor: EncoderVendor, codec: EncoderKind) -> EncoderCandidate:
    """Run a 0.1s null encode through this encoder; record success/failure."""
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "nullsrc=s=256x256:r=10:d=0.1",
        "-c:v",
        name,
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return EncoderCandidate(
            name=name, vendor=vendor, codec=codec, works=False, error="timeout"
        )
    except FileNotFoundError as exc:
        return EncoderCandidate(
            name=name, vendor=vendor, codec=codec, works=False, error=str(exc)
        )

    if proc.returncode == 0:
        return EncoderCandidate(name=name, vendor=vendor, codec=codec, works=True)

    err_tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    err_msg = err_tail[-1] if err_tail else f"exit {proc.returncode}"
    return EncoderCandidate(
        name=name, vendor=vendor, codec=codec, works=False, error=err_msg[:200]
    )


def _ffmpeg_version_hash() -> str:
    try:
        out = subprocess.check_output(
            [ffmpeg_bin(), "-version"], text=True, timeout=5
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise EncoderError(f"failed to query ffmpeg --version: {exc}") from exc
    return hashlib.sha256(out.encode("utf-8")).hexdigest()[:16]


def _load_cache(version_key: str) -> list[EncoderCandidate] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("version_key") != version_key:
        return None
    if time.time() - raw.get("written_at", 0) > CACHE_TTL_SEC:
        return None
    return [EncoderCandidate.model_validate(c) for c in raw.get("candidates", [])]


def _save_cache(version_key: str, candidates: list[EncoderCandidate]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "version_key": version_key,
        "written_at": time.time(),
        "candidates": [c.model_dump() for c in candidates],
    }
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, CACHE_PATH)
