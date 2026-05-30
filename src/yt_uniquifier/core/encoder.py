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

CACHE_TTL_SEC = 7 * 24 * 3600


def _cache_path() -> Path:
    """Return the encoder cache path.

    Reads the module-level ``CACHE_PATH`` so test fixtures can redirect
    it via ``monkeypatch.setattr(..., "CACHE_PATH", tmp)``. If a fresh
    process needs to honor a `HOME` change made after import, reassign
    ``CACHE_PATH`` directly instead of mutating the env.
    """
    import sys as _sys
    from typing import cast
    return cast(Path, _sys.modules[__name__].CACHE_PATH)


# Default cache location resolved at import. Tests / tooling can
# reassign this constant (or monkeypatch it) to redirect the cache.
CACHE_PATH = Path.home() / ".cache" / "yt_uniquifier" / "encoders.json"

# Vendor-default concurrent encode session counts when we can't query
# anything more specific (e.g. nvidia-smi missing).
_VENDOR_DEFAULT_PARALLEL: dict[str, int] = {
    "nvenc": 3,          # consumer NVENC driver limit
    "qsv": 2,
    "amf": 2,
    "videotoolbox": 2,
    "x264": 0,           # 0 = compute from cpu_count() // 2
    "x265": 0,
}

# NVIDIA chip names that signal a pro/datacenter card with no NVENC session cap.
_NVIDIA_PRO_TOKENS = ("quadro", "rtx a", "tesla", "a100", "h100", "l40", "rtx 6000")

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


def _detect_max_parallel(vendor: EncoderVendor) -> int:
    """Best-effort per-vendor concurrent-session count.

    Queries nvidia-smi for NVENC; uses cpu_count() for x264/x265; falls back
    to a conservative default for the rest. Always >= 1.
    """
    if vendor == "nvenc":
        return _nvenc_max_parallel()
    if vendor in ("x264", "x265"):
        import os
        return max(1, (os.cpu_count() or 2) // 2)
    return _VENDOR_DEFAULT_PARALLEL.get(vendor, 1)


def _nvenc_max_parallel() -> int:
    """Parse nvidia-smi output; cap at 3 for consumer, 8 for pro/Quadro."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    if proc.returncode != 0 or not proc.stdout.strip():
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    first_line = proc.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first_line.split(",", 1)]
    if len(parts) != 2:
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    try:
        free_mb = int(parts[0])
    except ValueError:
        return _VENDOR_DEFAULT_PARALLEL["nvenc"]
    name = parts[1].lower()
    is_pro = any(tok in name for tok in _NVIDIA_PRO_TOKENS)
    cap = 8 if is_pro else 3
    # ~500 MB per concurrent 1080p NVENC session is a safe rule of thumb.
    by_vram = max(1, free_mb // 500)
    return min(cap, by_vram)


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
        return EncoderCandidate(
            name=name, vendor=vendor, codec=codec, works=True,
            max_parallel=_detect_max_parallel(vendor),
        )

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
    cache_path = _cache_path()
    if not cache_path.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("version_key") != version_key:
        return None
    if time.time() - raw.get("written_at", 0) > CACHE_TTL_SEC:
        return None
    return [EncoderCandidate.model_validate(c) for c in raw.get("candidates", [])]


def _save_cache(version_key: str, candidates: list[EncoderCandidate]) -> None:
    cache_path = _cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "version_key": version_key,
        "written_at": time.time(),
        # mode="json" coerces any Enum/Path/datetime fields to JSON-native
        # form. Today EncoderCandidate uses Literal types so plain
        # model_dump() works, but a future Enum field would silently
        # break json.dumps. Consistent with checkpoint.py + compute_plan_hash.
        "candidates": [c.model_dump(mode="json") for c in candidates],
    }
    # PID-suffixed tmp + fsync. Concurrent `yt-uniq batch` processes share
    # this cache path; without per-process tmp names, two probes racing on
    # `encoders.json.tmp` can land in a torn write whose stale read makes
    # the other process silently miss encoders. fsync forces the bytes
    # to disk before the rename so a crash mid-flush leaves either the
    # old cache or the new one, never a zero-byte file.
    tmp = cache_path.with_name(f"encoders.{os.getpid()}.tmp")
    serialised = json.dumps(payload, indent=2)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(serialised)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, cache_path)
