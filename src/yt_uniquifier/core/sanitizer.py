"""Second-pass libx264 re-encode to strip encoder-family bitstream signatures.

After the main pipeline produces output.mp4 (potentially via NVENC, QSV,
AMF, or VideoToolbox), this module optionally re-encodes through libx264
with stock parameters. Audio passes through with stream copy.

The goal is NOT additional perceptual divergence — that's the job of the
transform pipeline. The goal is to make the *file-level encoder
signature* match the modal "creator uploads from a laptop with stock
ffmpeg" pattern, so output is indistinguishable from a generic libx264
upload at the bitstream level (slice headers, CABAC tables, etc.).

Opt-in via `yt-uniq run --sanitize-bitstream`. For users who don't care
about encoder fingerprinting, it adds ~30-60 min wall time on a 2 h
source plus ~3 VMAF points of quality drop for no benefit.

For libx264-source runs it's a no-op (output is already libx264).
HEVC and HDR-keep paths refuse sanitization with a clear error —
re-encoding HEVC to H.264 would destroy quality unintentionally, and
libx264 has no 10-bit profile for HDR.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import time
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import EncoderCandidate
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

# Upper bound on a full second-pass libx264 re-encode of a long file
# (~2h source on slow hardware). The Popen poll loop checks
# cancel_token every SANITIZE_POLL_SEC seconds so a cooperative
# cancel responds in seconds, not at this hard timeout.
SANITIZE_TIMEOUT_SEC = 3600
SANITIZE_POLL_SEC = 0.5

# CRF 20 is one notch below the source pipeline's CRF 18, giving ~3 VMAF
# points drop in exchange for a clean libx264 bitstream.
SANITIZE_CRF = 20
SANITIZE_PRESET = "medium"


def needs_sanitization(encoder: EncoderCandidate) -> bool:
    """True if the produced bitstream came from a non-libx264 encoder."""
    return encoder.vendor != "x264"


def reject_for_hdr_or_hevc(plan_keep_hdr: bool, encoder: EncoderCandidate) -> None:
    """Raise if sanitization would corrupt the output's intended characteristics.

    libx264 has no 10-bit profile; HDR keep-hdr would lose its color
    information. HEVC source through libx264 re-encode is also usually
    not the user's intent — refuse explicitly so they re-think.
    """
    if plan_keep_hdr:
        raise PipelineError(
            "sanitize-bitstream requires SDR/8-bit output; profile has "
            "keep_hdr=true. Drop --sanitize-bitstream or use "
            "video.tonemap_sdr in the profile to collapse HDR to SDR first."
        )
    if encoder.codec == "hevc":
        raise PipelineError(
            f"sanitize-bitstream re-encodes via libx264 (h264). Current "
            f"encoder is {encoder.name} (hevc). Re-encoding HEVC to H.264 "
            f"may not be the intent; remove --sanitize-bitstream or set "
            f"profile.target_codec=h264."
        )


def sanitize_bitstream(
    input_path: Path,
    output_path: Path,
    *,
    cancel_token: CancelToken | None = None,
) -> None:
    """Re-encode input → output via libx264 stock; audio stream-copied.

    Both paths can be the same file — we write to a `.sanitized.mp4` temp
    next to output_path, then atomic-rename.

    Audio (`-c:a copy`), subtitles, chapters all passed through.

    Cooperative cancellation: when ``cancel_token`` fires, the running
    ffmpeg child is sent SIGINT (then SIGKILL after a grace period) and
    a ``PipelineError("cancelled by user")`` is raised. Without this,
    a sanitize phase on a 2 h file would block the cancel signal for
    up to an hour.
    """
    if not input_path.exists():
        raise PipelineError(f"sanitize: input not found: {input_path}")

    tmp = output_path.with_suffix(".sanitized.mp4")
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", SANITIZE_PRESET,
        "-crf", str(SANITIZE_CRF),
        "-pix_fmt", "yuv420p",  # 8-bit only; HDR refused upstream
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:a", "copy",
        "-map", "0:s?",
        "-c:s", "copy",
        "-map_chapters", "0",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
        str(tmp),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + SANITIZE_TIMEOUT_SEC
    cancelled = False
    while True:
        try:
            proc.wait(timeout=SANITIZE_POLL_SEC)
            break
        except subprocess.TimeoutExpired:
            if cancel_token is not None and cancel_token.is_cancelled():
                cancelled = True
                _terminate(proc)
                break
            if time.monotonic() >= deadline:
                _terminate(proc)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise PipelineError(
                    f"sanitize_bitstream exceeded {SANITIZE_TIMEOUT_SEC}s"
                ) from None
    stderr_data = ""
    try:
        _, stderr_data = proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        stderr_data = ""
    if cancelled:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise PipelineError("cancelled by user")
    if proc.returncode != 0:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise PipelineError(
            f"sanitize_bitstream failed: {stderr_data.strip()[-500:]}"
        )

    tmp.replace(output_path)


def _terminate(proc: subprocess.Popen[str], wait_sec: float = 5.0) -> None:
    """Try SIGINT, wait, then SIGKILL — same pattern as runner._terminate."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.send_signal(signal.SIGINT)
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
