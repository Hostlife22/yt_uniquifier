"""Detect working FFmpeg encoders and apply an explicit selection policy.

Strategy: enumerate a fixed candidate list (NVENC, QSV, VideoToolbox, AMF, x264/x265)
and run a short null-output encode for each. Cache the result keyed by ffmpeg --version,
device/driver signature, so repeated `yt-uniq` invocations don't re-probe. Automatic
selection is quality-first; balanced/speed are explicit operator choices.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from yt_uniquifier.core.errors import EncoderError
from yt_uniquifier.core.models import EncoderCandidate, EncoderKind, EncoderVendor
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

if TYPE_CHECKING:
    from yt_uniquifier.core.models import Plan
    from yt_uniquifier.core.runner import CancelToken

CACHE_TTL_SEC = 7 * 24 * 3600
ENCODER_CACHE_SCHEMA_VERSION = 2
ENCODER_POLICY_ENV = "YT_UNIQ_ENCODER_POLICY"
EncoderPolicy = Literal["quality", "balanced", "speed"]
_ENCODER_POLICIES = frozenset({"quality", "balanced", "speed"})


@dataclass(frozen=True)
class EncoderCapabilityResult:
    supported: bool
    width: int
    height: int
    pix_fmt: str
    error: str | None = None


_CAPABILITY_CACHE: dict[str, EncoderCapabilityResult] = {}


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
    # v1.2.0 Task 22: AV1 software encoders.  libaom-av1 is single-threaded
    # within an encode but the orchestrator already parallelises across
    # segments, so we still treat it like x264/x265 (0 = cpu_count() // 2).
    "libaom": 0,
    "svtav1": 0,
}

# NVIDIA chip names that signal a pro/datacenter card with no NVENC session cap.
_NVIDIA_PRO_TOKENS = ("quadro", "rtx a", "tesla", "a100", "h100", "l40", "rtx 6000")

# Canonical discovery order only. Selection policy is defined separately below;
# probe ordering must not accidentally decide production output quality.
_CANDIDATES: tuple[tuple[str, EncoderVendor, EncoderKind], ...] = (
    ("h264_nvenc", "nvenc", "h264"),
    ("hevc_nvenc", "nvenc", "hevc"),
    ("h264_qsv", "qsv", "h264"),
    ("hevc_qsv", "qsv", "hevc"),
    ("h264_videotoolbox", "videotoolbox", "h264"),
    ("hevc_videotoolbox", "videotoolbox", "hevc"),
    ("h264_amf", "amf", "h264"),
    ("hevc_amf", "amf", "hevc"),
    # av1_vulkan is intentionally not advertised yet. FFmpeg requires Vulkan
    # hardware frames (init_hw_device + hwupload), while this pipeline currently
    # produces CPU filter frames. A cheap CPU-frame probe therefore cannot prove
    # that the real graph is runnable. Keep the vendor contract for old plans,
    # but do not auto-select it until an end-to-end upload path is implemented.
    # v1.2.0 Task 22 — AV1 hardware paths. All four reuse the same vendor
    # tag as their h264/hevc
    # siblings — the CRF/quality knobs are identical (cq, global_quality,
    # qp_i/qp_p, b:v).
    ("av1_nvenc", "nvenc", "av1"),
    ("av1_qsv", "qsv", "av1"),
    ("av1_amf", "amf", "av1"),
    ("av1_videotoolbox", "videotoolbox", "av1"),
    ("libx264", "x264", "h264"),
    ("libx265", "x265", "hevc"),
    # v1.2.0 Task 22 — AV1 software encoders.  libsvtav1 is the default
    # CPU choice (Intel SVT-AV1, ~3× libx264 wall-clock and very widely
    # available in modern ffmpeg builds).  libaom-av1 is the reference
    # implementation, ~10× slower but compression-optimal.
    ("libsvtav1", "svtav1", "av1"),
    ("libaom-av1", "libaom", "av1"),
)


_POLICY_ORDER: dict[EncoderPolicy, dict[EncoderKind, tuple[str, ...]]] = {
    # Production default: prefer deterministic, mature software encoders. For
    # AV1, libaom is the compression-quality choice; operators processing long
    # material can choose balanced (SVT-AV1) or speed (hardware) explicitly.
    "quality": {
        "h264": ("libx264", "h264_nvenc", "h264_qsv", "h264_videotoolbox", "h264_amf"),
        "hevc": ("libx265", "hevc_nvenc", "hevc_qsv", "hevc_videotoolbox", "hevc_amf"),
        "av1": (
            "libaom-av1", "libsvtav1", "av1_nvenc", "av1_qsv",
            "av1_videotoolbox", "av1_amf",
        ),
    },
    # Balanced keeps the predictable x26x choices and avoids reference-slow
    # libaom for AV1. This is the practical long-form CPU policy.
    "balanced": {
        "h264": ("libx264", "h264_nvenc", "h264_qsv", "h264_videotoolbox", "h264_amf"),
        "hevc": ("libx265", "hevc_nvenc", "hevc_qsv", "hevc_videotoolbox", "hevc_amf"),
        "av1": (
            "libsvtav1", "av1_nvenc", "av1_qsv", "av1_videotoolbox",
            "av1_amf", "libaom-av1",
        ),
    },
    # Speed favours verified hardware paths, retaining a software fallback.
    "speed": {
        "h264": ("h264_nvenc", "h264_qsv", "h264_videotoolbox", "h264_amf", "libx264"),
        "hevc": ("hevc_nvenc", "hevc_qsv", "hevc_videotoolbox", "hevc_amf", "libx265"),
        "av1": (
            "av1_nvenc", "av1_qsv", "av1_videotoolbox", "av1_amf",
            "libsvtav1", "libaom-av1",
        ),
    },
}


def resolve_encoder_policy(policy: str | None = None) -> EncoderPolicy:
    """Resolve and validate encoder selection policy.

    The environment hook keeps one policy consistent across CLI, GUI, web, and
    distributed workers without duplicating frontend options. An explicit encoder
    override still wins over policy and is validated strictly by its caller.
    """
    value = (policy if policy is not None else os.environ.get(ENCODER_POLICY_ENV, "quality"))
    value = value.strip().lower()
    if value not in _ENCODER_POLICIES:
        allowed = ", ".join(sorted(_ENCODER_POLICIES))
        raise EncoderError(
            f"invalid encoder policy {value!r}; expected one of: {allowed} "
            f"(configure with {ENCODER_POLICY_ENV})"
        )
    return cast(EncoderPolicy, value)


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
    # Capture once. GUI/background discovery may overlap a caller that swaps
    # the configured cache location; resolving the module global again after
    # all probes can redirect an in-flight write into another run's cache.
    cache_path = _cache_path()
    version_key = _ffmpeg_version_hash()
    if not force:
        cached = _load_cache(version_key, cache_path=cache_path)
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
    _save_cache(version_key, results, cache_path=cache_path)
    return results




def pick_encoder(
    candidates: Sequence[EncoderCandidate],
    *,
    prefer: Sequence[str] | None = None,
    codec: EncoderKind = "h264",
    policy: str | None = None,
    require_preferred: bool = False,
) -> EncoderCandidate:
    """Pick a working encoder matching codec and the requested policy.

    Explicit ``prefer`` entries win in order. When ``require_preferred`` is true,
    failure to select one is an error rather than a silent automatic fallback.
    Otherwise, ``quality`` (the production default), ``balanced``, or ``speed``
    determines the automatic order.
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
        if require_preferred:
            all_by_name = {c.name: c for c in candidates}
            reasons: list[str] = []
            for name in prefer:
                candidate = all_by_name.get(name)
                if candidate is None:
                    reasons.append(f"{name}: unknown encoder")
                elif candidate.codec != codec:
                    reasons.append(
                        f"{name}: codec is {candidate.codec}, requested profile needs {codec}"
                    )
                else:
                    reasons.append(f"{name}: {candidate.error or 'probe failed'}")
            raise EncoderError(
                "requested encoder override is unavailable: " + "; ".join(reasons)
            )

    resolved_policy = resolve_encoder_policy(policy)
    policy_order = _POLICY_ORDER[resolved_policy][codec]
    by_name = {c.name: c for c in working}
    for name in policy_order:
        if name in by_name:
            return by_name[name]

    # Future/plugin candidates may not yet have a policy rank. Preserve their
    # discovery order as a compatibility fallback after every known encoder.
    return working[0]


def _detect_max_parallel(vendor: EncoderVendor) -> int:
    """Best-effort per-vendor concurrent-session count.

    Queries nvidia-smi for NVENC; uses cpu_count() for x264/x265; falls back
    to a conservative default for the rest. Always >= 1.
    """
    if vendor == "nvenc":
        return _nvenc_max_parallel()
    if vendor in ("x264", "x265", "libaom", "svtav1"):
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
    ]
    # The reference libaom defaults are intentionally compression-heavy: on the
    # Intel Mac qualification host this eight-frame discovery clip took 15.63 s,
    # just beyond the 15 s availability timeout. Probe-only speed knobs make the
    # same capability check finish in ~1.5 s without changing production argv.
    if vendor == "libaom":
        cmd.extend(["-cpu-used", "8", "-row-mt", "1"])
    cmd.extend(["-f", "null", "-"])
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
    # Encoder availability is a function of both the ffmpeg binary and the
    # active device/driver. A week-long cache keyed only on `ffmpeg -version`
    # survived GPU driver updates and CUDA_VISIBLE_DEVICES changes.
    signature_parts = [
        out,
        platform.system(),
        platform.release(),
        platform.version(),
        platform.machine(),
        os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        os.environ.get("GPU_DEVICE_ORDINAL", ""),
    ]
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            gpu = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=uuid,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            signature_parts.append(gpu.stdout if gpu.returncode == 0 else gpu.stderr)
        except (OSError, subprocess.TimeoutExpired):
            signature_parts.append("nvidia-smi-unavailable")
    return hashlib.sha256("\n".join(signature_parts).encode("utf-8")).hexdigest()[:16]


def probe_encoder_for_plan(plan: Plan) -> EncoderCapabilityResult:
    """Verify the selected encoder against the actual job output contract."""
    from yt_uniquifier.core.pipeline import (
        _segment_pix_fmt,
        build_encoder_capability_probe,
    )

    video = plan.source.video[0]
    cmd = build_encoder_capability_probe(plan)
    width, height = video.width // 2 * 2, video.height // 2 * 2
    source_arg = next((arg for arg in cmd if arg.startswith("testsrc2=s=")), "")
    try:
        dims = source_arg.split("=", 2)[2].split(":", 1)[0]
        width, height = (int(value) for value in dims.split("x", 1))
    except (IndexError, ValueError):
        pass
    pix_fmt = _segment_pix_fmt(plan)
    key_payload = json.dumps(
        {"version": _ffmpeg_version_hash(), "cmd": cmd[1:]},
        sort_keys=True,
    )
    cache_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
    cached = _CAPABILITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        result = EncoderCapabilityResult(
            supported=False,
            width=width,
            height=height,
            pix_fmt=pix_fmt,
            error="capability probe timed out after 45 seconds",
        )
    except OSError as exc:
        result = EncoderCapabilityResult(
            supported=False,
            width=width,
            height=height,
            pix_fmt=pix_fmt,
            error=str(exc),
        )
    else:
        error_lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        result = EncoderCapabilityResult(
            supported=proc.returncode == 0,
            width=width,
            height=height,
            pix_fmt=pix_fmt,
            error=(error_lines[-1][:300] if error_lines else None),
        )
    # Cache successes only. Hardware-session exhaustion is transient; retaining
    # a failed probe for the lifetime of a web/worker process would keep rejecting
    # every later job even after the device becomes available again.
    if result.supported:
        _CAPABILITY_CACHE[cache_key] = result
    return result


def _load_cache(
    version_key: str, *, cache_path: Path | None = None,
) -> list[EncoderCandidate] | None:
    cache_path = cache_path or _cache_path()
    if not cache_path.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("schema_version") != ENCODER_CACHE_SCHEMA_VERSION:
        return None
    if raw.get("version_key") != version_key:
        return None
    if time.time() - raw.get("written_at", 0) > CACHE_TTL_SEC:
        return None
    return [EncoderCandidate.model_validate(c) for c in raw.get("candidates", [])]


def _save_cache(
    version_key: str,
    candidates: list[EncoderCandidate],
    *,
    cache_path: Path | None = None,
) -> None:
    cache_path = cache_path or _cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": ENCODER_CACHE_SCHEMA_VERSION,
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
