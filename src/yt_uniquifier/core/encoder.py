"""Detect which ffmpeg encoders actually work on this machine.

Strategy: enumerate a fixed candidate list (NVENC, QSV, VideoToolbox, AMF, x264/x265)
and run a short null-output encode for each. Cache the result keyed by ffmpeg --version,
so repeated `yt-uniq` invocations don't re-probe.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yt_uniquifier.core.errors import EncoderError
from yt_uniquifier.core.models import EncoderCandidate, EncoderKind, EncoderVendor
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

if TYPE_CHECKING:
    from yt_uniquifier.core.runner import CancelToken

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
    # F8 (v0.6.0): Vulkan AV1 encoder shipped in FFmpeg 8.0 (Aug 2025).
    # No published per-driver session cap yet; treat as moderate.
    "vulkan": 2,
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
    # F8 (v0.6.0): Vulkan AV1 (FFmpeg 8.0 "Huffman"). Cross-vendor —
    # works on AMD, Intel, and recent NVIDIA without vendor-specific
    # NVENC/QSV/AMF paths. The probe is the same lavfi null-source
    # test as the others; pick_encoder still respects user `prefer`.
    ("av1_vulkan", "vulkan", "av1"),
    ("libx264", "x264", "h264"),
    ("libx265", "x265", "hevc"),
)


def detect_encoders(
    force: bool = False,
    *,
    cancel_token: CancelToken | None = None,
) -> list[EncoderCandidate]:
    """Return list of encoder candidates with works=True/False.

    Cached at CACHE_PATH keyed by sha256 of `ffmpeg -version` output.
    Pass force=True to bypass the cache and re-probe.

    A6 (v0.5.5): ``cancel_token`` is checked between probes. Pre-fix
    the GUI ``EncoderDetectWorker.request_cancel()`` was a silent
    no-op — a cold-cache probe spawns up to 10 sequential ffmpeg
    subprocesses (~5-15 s), during which Cancel did nothing.
    """
    version_key = _ffmpeg_version_hash()
    if not force:
        cached = _load_cache(version_key)
        if cached is not None:
            return cached

    # B6 (v0.6.0): parallel probe. Each _probe_one spawns an ffmpeg
    # subprocess (~0.5 s) and they are independent — fan out via a
    # ThreadPoolExecutor capped at len(_CANDIDATES). On a cold cache
    # this drops the wall-clock from ~5.5 s (10 serial probes) to
    # ~0.6 s.
    if cancel_token is not None and cancel_token.is_cancelled():
        from yt_uniquifier.core.errors import PipelineError
        raise PipelineError("encoder detection cancelled by user")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(_CANDIDATES)) as pool:
        futures = {
            pool.submit(_probe_one, name, vendor, codec): (name, vendor, codec)
            for name, vendor, codec in _CANDIDATES
        }
        # Preserve canonical order of _CANDIDATES in the result list so
        # pick_encoder's preference logic stays deterministic.
        by_key: dict[tuple[str, str, str], EncoderCandidate] = {}
        for fut, key in futures.items():
            if cancel_token is not None and cancel_token.is_cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                from yt_uniquifier.core.errors import PipelineError
                raise PipelineError("encoder detection cancelled by user")
            by_key[key] = fut.result()
    results = [by_key[(name, vendor, codec)] for name, vendor, codec in _CANDIDATES]
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
    """Parse nvidia-smi output; sum capacity across every visible GPU.

    B7 (v0.6.0): pre-fix only the first GPU's capacity was returned.
    A multi-GPU box (e.g. two RTX A6000s) reported the cap of one card
    instead of the union, halving achievable parallelism on
    distributed-batch workers that span both GPUs via the runtime's
    own scheduling.

    Per-GPU formula: cap = 8 if pro/Quadro/datacenter else 3, then
    ``min(cap, free_mb // 500)``. Sum across GPUs. Caller of
    ``_nvenc_max_parallel`` is still ``EncoderCandidate.max_parallel``
    which the orchestrator clamps user-requested ``--workers`` against.
    """
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

    total = 0
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",", 1)]
        if len(parts) != 2:
            continue
        try:
            free_mb = int(parts[0])
        except ValueError:
            continue
        name = parts[1].lower()
        is_pro = any(tok in name for tok in _NVIDIA_PRO_TOKENS)
        cap = 8 if is_pro else 3
        # ~500 MB per concurrent 1080p NVENC session is a safe rule of thumb.
        by_vram = max(1, free_mb // 500)
        total += min(cap, by_vram)
    return max(1, total) if total else _VENDOR_DEFAULT_PARALLEL["nvenc"]


def _probe_one(name: str, vendor: EncoderVendor, codec: EncoderKind) -> EncoderCandidate:
    """Run a 0.5s test-pattern encode through this encoder; record success/failure.

    Previously used ``nullsrc=s=256x256`` which produced a single black
    frame at sub-VideoToolbox-minimum dimensions, causing h264_videotoolbox
    to report ``Nothing was written into output file, because at least
    one of its streams received no packets`` on macOS — making the
    user-visible probe falsely flag h264_videotoolbox as broken. The
    2026-05-31 real-video sweep caught this on a Mac with working
    VideoToolbox: probe said `works=false`, but a manual encode via
    `--encoder h264_videotoolbox` succeeded.

    ``testsrc2=s=640x360:r=15:d=0.5`` produces 7-8 frames of a real
    test pattern at a resolution every hardware encoder accepts.
    ``-pix_fmt yuv420p`` is explicit so encoders that demand a specific
    chroma layout (VideoToolbox) don't reject the input.
    """
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=640x360:r=15:d=0.5",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        name,
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
    # PID + random-suffixed tmp + fsync. Concurrent `yt-uniq batch` processes
    # share this cache path; without per-process tmp names, two probes racing
    # on `encoders.json.tmp` can land in a torn write whose stale read makes
    # the other process silently miss encoders. The random suffix additionally
    # avoids any same-process collision when `_save_cache` is called twice
    # back-to-back (e.g. test_force_bypasses_cache on Linux py3.12 was racing
    # on the static PID-only name). fsync forces the bytes to disk before the
    # rename so a crash mid-flush leaves either the old cache or the new one,
    # never a zero-byte file.
    tmp = cache_path.with_name(
        f"encoders.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    serialised = json.dumps(payload, indent=2)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(serialised)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, cache_path)
