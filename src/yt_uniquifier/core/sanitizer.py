"""Optional second-pass libx264 normalization for H.264 interoperability.

After the main pipeline produces output.mp4 (potentially via NVENC, QSV,
AMF, or VideoToolbox), this module optionally re-encodes through libx264
with stock parameters. Audio passes through with stream copy.

The pass is opt-in via `yt-uniq run --sanitize-bitstream` and exists only for
workflows that require a uniform libx264 delivery codec across heterogeneous
authorized sources. It adds ~30-60 min wall time on a 2 h source and causes
generation loss, so it is not part of the production quality-first default.

For libx264-source runs it's a no-op (output is already libx264).
HEVC and HDR-keep paths refuse sanitization with a clear error —
re-encoding HEVC to H.264 would destroy quality unintentionally, and
libx264 has no 10-bit profile for HDR.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import EncoderCandidate
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

# CRF 20 is one notch below the source pipeline's CRF 18, giving ~3 VMAF
# points drop in exchange for a clean libx264 bitstream.
SANITIZE_CRF = 20
SANITIZE_PRESET = "medium"


def needs_sanitization(encoder: EncoderCandidate) -> bool:
    """True when an H.264 output did not already use libx264."""
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
    if encoder.codec != "h264":
        raise PipelineError(
            f"sanitize-bitstream re-encodes via libx264 (h264), but the "
            f"selected encoder is {encoder.name} ({encoder.codec}). Changing "
            f"the requested codec is not allowed; remove --sanitize-bitstream "
            f"or set profile.target_codec=h264."
        )


def sanitize_bitstream(
    input_path: Path,
    output_path: Path,
    *,
    cancel_token: CancelToken | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
) -> None:
    """Re-encode input → output via libx264 stock; audio stream-copied.

    Both paths can be the same file. A unique sibling temporary file retains
    the requested container suffix and is atomically renamed on success.

    Audio (`-c:a copy`), subtitles, chapters all passed through.

    Execution goes through the shared FFmpeg runner, so process-tree
    cancellation, bounded log retention, and the configurable stall watchdog
    are identical to the main pipeline. There is no fixed one-hour wall limit.
    """
    if not input_path.exists():
        raise PipelineError(f"sanitize: input not found: {input_path}")

    suffix = output_path.suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv"}:
        raise PipelineError(
            f"sanitize: unsupported target container suffix {suffix or '<none>'!r}"
        )
    tmp = output_path.with_name(
        f".{output_path.stem}.sanitized.{os.getpid()}.{secrets.token_hex(4)}{suffix}"
    )
    args = [
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
        "-map_metadata", "0",
    ]
    if suffix in {".mp4", ".mov"}:
        args += ["-movflags", "+faststart"]
    args.append(str(tmp))
    command = BuiltCommand(args=args)
    try:
        run_ffmpeg(
            command,
            output=tmp,
            on_event=on_event,
            cancel_token=cancel_token,
        )
    except BaseException:
        if tmp != input_path:
            tmp.unlink(missing_ok=True)
        raise

    tmp.replace(output_path)
