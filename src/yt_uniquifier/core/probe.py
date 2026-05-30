"""Probe a media file into a SourceMeta via a single ffprobe call.

No OpenCV fallback (the legacy prototype used cv2; for long files that path is
unnecessary — ffprobe handles every container we care about).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from yt_uniquifier.core.errors import ProbeError
from yt_uniquifier.core.models import (
    AudioStream,
    Chapter,
    ColorPrimaries,
    ColorRange,
    ColorSpace,
    ColorTransfer,
    HDRInfo,
    SourceMeta,
    SubtitleStream,
    VideoStream,
)
from yt_uniquifier.core.utils.ffmpeg_paths import ffprobe_bin

_HDR_TRANSFERS: set[ColorTransfer] = {"smpte2084", "arib-std-b67"}
_IMAGE_SUB_CODECS = {
    "hdmv_pgs_subtitle",
    "pgs",
    "dvd_subtitle",
    "dvb_subtitle",
    "dvb_teletext",
}


def probe(path: Path) -> SourceMeta:
    """Run ffprobe and return a structured SourceMeta. Raises ProbeError on failure."""
    if not path.exists():
        raise ProbeError(f"input file does not exist: {path}")

    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise ProbeError(f"ffprobe failed for {path}: {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out for {path}") from exc

    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe produced invalid JSON: {exc}") from exc

    return _parse(raw, path)


def _parse(raw: dict[str, Any], path: Path) -> SourceMeta:
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])
    chapters_raw = raw.get("chapters", [])

    video: list[VideoStream] = []
    audio: list[AudioStream] = []
    subtitle: list[SubtitleStream] = []

    for s in streams:
        kind = s.get("codec_type")
        if kind == "video":
            video.append(_parse_video(s, fmt))
        elif kind == "audio":
            audio.append(_parse_audio(s))
        elif kind == "subtitle":
            subtitle.append(_parse_subtitle(s))
        # data/attachment streams ignored

    duration = _to_float(fmt.get("duration"), 0.0)
    size_bytes = _to_int(fmt.get("size"), 0)
    container = _normalize_container(fmt.get("format_name", ""))

    chapters = [_parse_chapter(c) for c in chapters_raw]

    return SourceMeta(
        path=path,
        container=container,
        duration_sec=duration,
        size_bytes=size_bytes,
        video=video,
        audio=audio,
        subtitle=subtitle,
        chapters=chapters,
    )


def _parse_video(s: dict[str, Any], fmt: dict[str, Any]) -> VideoStream:
    transfer = _norm(s.get("color_transfer"), "unknown")
    primaries = _norm(s.get("color_primaries"), "unknown")
    space = _norm(s.get("color_space"), "unknown")
    crange = _norm(s.get("color_range"), "unknown")

    bit_depth = _to_int(s.get("bits_per_raw_sample"), 8)
    if bit_depth == 0:
        pix_fmt = s.get("pix_fmt", "") or ""
        bit_depth = 10 if "10" in pix_fmt else 8

    color = HDRInfo(
        is_hdr=transfer in _HDR_TRANSFERS,
        transfer=_coerce_transfer(transfer),
        primaries=_coerce_primaries(primaries),
        space=_coerce_space(space),
        color_range=_coerce_range(crange),
        bit_depth=bit_depth,
    )

    duration = _to_float(
        s.get("duration") or fmt.get("duration"),
        0.0,
    )

    return VideoStream(
        index=_to_int(s.get("index"), 0),
        codec=s.get("codec_name", "") or "",
        width=_to_int(s.get("width"), 0),
        height=_to_int(s.get("height"), 0),
        fps=_parse_fps(s.get("r_frame_rate") or s.get("avg_frame_rate")),
        duration_sec=duration,
        pix_fmt=s.get("pix_fmt", "") or "",
        bit_rate=_to_int_or_none(s.get("bit_rate")),
        color=color,
        is_default=bool(s.get("disposition", {}).get("default", 0)),
    )


def _parse_audio(s: dict[str, Any]) -> AudioStream:
    return AudioStream(
        index=_to_int(s.get("index"), 0),
        codec=s.get("codec_name", "") or "",
        sample_rate=_to_int(s.get("sample_rate"), 0),
        channels=_to_int(s.get("channels"), 0),
        channel_layout=s.get("channel_layout"),
        bit_rate=_to_int_or_none(s.get("bit_rate")),
        language=(s.get("tags") or {}).get("language"),
        is_default=bool(s.get("disposition", {}).get("default", 0)),
    )


def _parse_subtitle(s: dict[str, Any]) -> SubtitleStream:
    codec = s.get("codec_name", "") or ""
    return SubtitleStream(
        index=_to_int(s.get("index"), 0),
        codec=codec,
        language=(s.get("tags") or {}).get("language"),
        is_image_based=codec in _IMAGE_SUB_CODECS,
    )


def _parse_chapter(c: dict[str, Any]) -> Chapter:
    return Chapter(
        start_sec=_to_float(c.get("start_time"), 0.0),
        end_sec=_to_float(c.get("end_time"), 0.0),
        title=(c.get("tags") or {}).get("title"),
    )


def _parse_fps(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num_s, den_s = value.split("/", 1)
        num = _to_float(num_s, 0.0)
        den = _to_float(den_s, 0.0)
        return num / den if den else 0.0
    return _to_float(value, 0.0)


def _parse_fraction(value: str) -> float:
    """Strict fraction parser — raises ProbeError on invalid input.

    Counterpart to ``_parse_fps``. Use where downstream logic genuinely
    needs a valid frame rate (segment math, encoder bitrate). The lenient
    ``_parse_fps`` returns 0 for attached-picture streams and other
    degenerate cases; the strict variant catches the same cases as a
    typed exception so callers don't silently propagate fps=0 into
    arithmetic that divides by it.
    """
    from yt_uniquifier.core.errors import ProbeError

    if not value or "/" not in value:
        raise ProbeError(f"invalid frame rate {value!r}; expected 'num/den'")
    num_s, den_s = value.split("/", 1)
    try:
        num = float(num_s)
        den = float(den_s)
    except ValueError as exc:
        raise ProbeError(f"invalid frame rate {value!r}: {exc}") from exc
    if den == 0:
        raise ProbeError(f"invalid frame rate {value!r}: zero denominator")
    return num / den


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm(value: Any, default: str) -> str:
    if value is None:
        return default
    s = str(value).strip().lower()
    return s or default


def _coerce_transfer(value: str) -> ColorTransfer:
    allowed: set[str] = {
        "bt709",
        "smpte2084",
        "arib-std-b67",
        "bt470bg",
        "smpte170m",
        "iec61966-2-1",
        "unknown",
    }
    return value if value in allowed else "unknown"  # type: ignore[return-value]


def _coerce_primaries(value: str) -> ColorPrimaries:
    allowed: set[str] = {"bt709", "bt2020", "bt470bg", "smpte170m", "smpte432", "unknown"}
    return value if value in allowed else "unknown"  # type: ignore[return-value]


def _coerce_space(value: str) -> ColorSpace:
    allowed: set[str] = {"bt709", "bt2020nc", "bt2020c", "bt470bg", "smpte170m", "unknown"}
    return value if value in allowed else "unknown"  # type: ignore[return-value]


def _coerce_range(value: str) -> ColorRange:
    allowed: set[str] = {"tv", "pc", "unknown"}
    return value if value in allowed else "unknown"  # type: ignore[return-value]


def _normalize_container(format_name: str) -> str:
    """ffprobe returns comma-joined list like 'mov,mp4,m4a,3gp,...'. Pick first known."""
    if not format_name:
        return "unknown"
    parts = [p.strip() for p in format_name.split(",")]
    for known in ("mp4", "mov", "mkv", "matroska", "webm", "avi"):
        if known in parts:
            return "mkv" if known == "matroska" else known
    return parts[0] if parts else "unknown"
