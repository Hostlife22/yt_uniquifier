"""Probe a media file into a SourceMeta via a single ffprobe call.

No OpenCV fallback (the legacy prototype used cv2; for long files that path is
unnecessary — ffprobe handles every container we care about).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

from yt_uniquifier.core.auxiliary_streams import (
    AuxiliaryKind,
    AuxiliaryStream,
    set_auxiliary_streams,
)
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

# MOV/MP4 muxers synthesize these handler names even when the source stream
# had no title. They are container boilerplate, not user metadata, and must not
# make the final media contract report a title that never existed.
_DEFAULT_MOV_HANDLER_NAMES = {"SoundHandler", "VideoHandler", "SubtitleHandler"}


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
        "-show_frames",
        "-read_intervals",
        "%+#1",
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
    frames = raw.get("frames", [])
    chapters_raw = raw.get("chapters", [])

    video: list[VideoStream] = []
    audio: list[AudioStream] = []
    subtitle: list[SubtitleStream] = []

    for s in streams:
        kind = s.get("codec_type")
        if kind == "video":
            if bool((s.get("disposition") or {}).get("attached_pic", 0)):
                continue
            first_frame: dict[str, Any] = next(
                (
                    frame for frame in frames
                    if frame.get("media_type") == "video"
                    and frame.get("stream_index") == s.get("index")
                ),
                {},
            )
            video.append(_parse_video(s, fmt, first_frame))
        elif kind == "audio":
            audio.append(_parse_audio(s))
        elif kind == "subtitle":
            subtitle.append(_parse_subtitle(s))

    duration = _to_float(fmt.get("duration"), 0.0)
    size_bytes = _to_int(fmt.get("size"), 0)
    container = _normalize_container(fmt.get("format_name", ""), path=path)

    chapters = [_parse_chapter(c) for c in chapters_raw]

    result = SourceMeta(
        path=path,
        container=container,
        duration_sec=duration,
        size_bytes=size_bytes,
        video=video,
        audio=audio,
        subtitle=subtitle,
        chapters=chapters,
    )
    set_auxiliary_streams(
        result,
        _parse_auxiliary_streams(streams, has_chapters=bool(chapters_raw)),
    )
    return result


def _parse_auxiliary_streams(
    streams: list[dict[str, Any]],
    *,
    has_chapters: bool = False,
) -> tuple[AuxiliaryStream, ...]:
    result: list[AuxiliaryStream] = []
    for stream in streams:
        raw_kind = stream.get("codec_type")
        is_attached_pic = (
            raw_kind == "video"
            and bool((stream.get("disposition") or {}).get("attached_pic", 0))
        )
        if raw_kind not in {"attachment", "data"} and not is_attached_pic:
            continue
        tags = stream.get("tags") or {}
        if (
            has_chapters
            and raw_kind == "data"
            and str(stream.get("codec_name") or "").lower() == "bin_data"
            and str(stream.get("codec_tag_string") or "").lower() == "text"
            and str(tags.get("handler_name") or "") == "SubtitleHandler"
        ):
            # FFmpeg's MOV/MP4 muxer synthesizes this data track as the
            # chapter representation. Chapters are parsed and validated via
            # SourceMeta.chapters, so counting their carrier again would
            # report a false unexpected auxiliary stream.
            continue
        kind = "attached_pic" if is_attached_pic else raw_kind
        title = tags.get("title") or tags.get("handler_name")
        result.append(AuxiliaryStream(
            index=_to_int(stream.get("index"), 0),
            kind=cast(AuxiliaryKind, kind),
            codec=str(stream.get("codec_name") or ""),
            codec_tag=str(stream.get("codec_tag_string") or ""),
            filename=_optional_string(tags.get("filename")),
            mimetype=_optional_string(tags.get("mimetype")),
            language=_optional_string(tags.get("language")),
            title=_optional_string(title),
            timecode=_optional_string(tags.get("timecode")),
        ))
    return tuple(result)


def _optional_string(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None


def _parse_video(
    s: dict[str, Any], fmt: dict[str, Any], first_frame: dict[str, Any]
) -> VideoStream:
    transfer = _norm(s.get("color_transfer"), "unknown")
    primaries = _norm(s.get("color_primaries"), "unknown")
    space = _norm(s.get("color_space"), "unknown")
    crange = _norm(s.get("color_range"), "unknown")

    bit_depth = _video_bit_depth(
        s.get("bits_per_raw_sample"),
        s.get("pix_fmt", "") or "",
    )

    side_data = [
        *s.get("side_data_list", []),
        *first_frame.get("side_data_list", []),
    ]
    mastering_display, max_cll, max_fall, dynamic_metadata = _parse_hdr_side_data(
        side_data,
    )
    color = HDRInfo(
        is_hdr=transfer in _HDR_TRANSFERS,
        transfer=_coerce_transfer(transfer),
        primaries=_coerce_primaries(primaries),
        space=_coerce_space(space),
        color_range=_coerce_range(crange),
        bit_depth=bit_depth,
        mastering_display=mastering_display,
        max_cll=max_cll,
        max_fall=max_fall,
        dynamic_metadata=dynamic_metadata,
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
        fps=_video_fps(s),
        duration_sec=duration,
        pix_fmt=s.get("pix_fmt", "") or "",
        bit_rate=_to_int_or_none(s.get("bit_rate")),
        color=color,
        is_default=bool(s.get("disposition", {}).get("default", 0)),
    )


def _parse_audio(s: dict[str, Any]) -> AudioStream:
    tags = s.get("tags") or {}
    return AudioStream(
        index=_to_int(s.get("index"), 0),
        codec=s.get("codec_name", "") or "",
        sample_rate=_to_int(s.get("sample_rate"), 0),
        channels=_to_int(s.get("channels"), 0),
        channel_layout=s.get("channel_layout"),
        bit_rate=_to_int_or_none(s.get("bit_rate")),
        language=tags.get("language"),
        # MOV/MP4 differs by FFmpeg version: current muxers expose `title`
        # as `name`, while FFmpeg 6.x reliably preserves `handler_name`.
        title=_stream_title(tags),
        is_default=bool(s.get("disposition", {}).get("default", 0)),
        dispositions=_parse_dispositions(s),
    )


def _parse_subtitle(s: dict[str, Any]) -> SubtitleStream:
    codec = s.get("codec_name", "") or ""
    tags = s.get("tags") or {}
    return SubtitleStream(
        index=_to_int(s.get("index"), 0),
        codec=codec,
        language=tags.get("language"),
        title=_stream_title(tags),
        is_image_based=codec in _IMAGE_SUB_CODECS,
        is_default=bool(s.get("disposition", {}).get("default", 0)),
        dispositions=_parse_dispositions(s),
    )


def _stream_title(tags: dict[str, Any]) -> str | None:
    title = tags.get("title") or tags.get("name")
    if title:
        return str(title)
    handler_name = tags.get("handler_name")
    if not handler_name or handler_name in _DEFAULT_MOV_HANDLER_NAMES:
        return None
    return str(handler_name)


def _parse_dispositions(stream: dict[str, Any]) -> tuple[str, ...]:
    raw = stream.get("disposition") or {}
    if not isinstance(raw, dict):
        return ()
    return tuple(sorted(
        str(name) for name, enabled in raw.items()
        if name != "attached_pic" and enabled == 1
    ))


def _parse_hdr_side_data(
    entries: list[dict[str, Any]],
) -> tuple[str | None, int | None, int | None, tuple[str, ...]]:
    mastering: str | None = None
    max_cll: int | None = None
    max_fall: int | None = None
    dynamic: set[str] = set()
    for entry in entries:
        side_type = str(entry.get("side_data_type", ""))
        side_lower = side_type.lower()
        if side_lower == "mastering display metadata":
            keys = (
                "green_x", "green_y", "blue_x", "blue_y", "red_x", "red_y",
                "white_point_x", "white_point_y", "max_luminance", "min_luminance",
            )
            if all(entry.get(key) is not None for key in keys):
                values = {
                    key: _fraction_to_scaled_int(
                        str(entry[key]), 10_000 if "luminance" in key else 50_000,
                    )
                    for key in keys
                }
                mastering = (
                    f"G({values['green_x']},{values['green_y']})"
                    f"B({values['blue_x']},{values['blue_y']})"
                    f"R({values['red_x']},{values['red_y']})"
                    f"WP({values['white_point_x']},{values['white_point_y']})"
                    f"L({values['max_luminance']},{values['min_luminance']})"
                )
        elif side_lower == "content light level metadata":
            max_cll = _to_int_or_none(entry.get("max_content"))
            max_fall = _to_int_or_none(entry.get("max_average"))
        elif (
            "dynamic hdr" in side_lower
            or "hdr dynamic" in side_lower
            or "dolby vision" in side_lower
            or "dovi" in side_lower
        ):
            dynamic.add(side_type)
    return mastering, max_cll, max_fall, tuple(sorted(dynamic))


def _fraction_to_scaled_int(value: str, scale: int) -> int:
    if "/" not in value:
        return round(float(value) * scale)
    numerator, denominator = value.split("/", 1)
    den = float(denominator)
    if den == 0:
        raise ProbeError(f"invalid HDR metadata fraction {value!r}")
    return round(float(numerator) / den * scale)


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


def _video_fps(stream: dict[str, Any]) -> float:
    """Return measured average cadence, falling back to the nominal rate.

    For VFR inputs FFprobe commonly reports ``r_frame_rate`` as the maximum or
    codec nominal cadence (for example 60) while ``avg_frame_rate`` reflects
    the decoded timeline (for example 36.67). Diagnostics and frame-based
    tolerances need the latter. Degenerate ``0/0`` averages still fall back to
    ``r_frame_rate`` for attached pictures and unusual containers.
    """
    for key in ("avg_frame_rate", "r_frame_rate"):
        parsed = _parse_fps(stream.get(key))
        if parsed > 0:
            return parsed
    return 0.0


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


def _video_bit_depth(bits_per_raw_sample: Any, pix_fmt: str) -> int:
    """Return component depth, including when ffprobe omits the explicit field.

    HEVC streams in MP4 commonly have no ``bits_per_raw_sample`` even though
    ``pix_fmt`` is unambiguously 10/12/16-bit. Treating the missing value as 8
    made HDR diagnostics disagree with the actual encoder contract.
    """
    explicit = _to_int(bits_per_raw_sample, 0)
    if explicit > 0:
        return explicit

    # Covers yuv420p10le, gbrp12be and hardware formats such as p010le.
    match = re.search(r"p0?(9|10|12|14|16)(?:le|be)?$", pix_fmt.lower())
    if match:
        return int(match.group(1))
    # Packed high-depth formats do not use the planar ``pNN`` suffix.
    packed_match = re.search(r"(?:gray|rgb|bgr|rgba|bgra)(10|12|14|16)", pix_fmt.lower())
    if packed_match:
        return int(packed_match.group(1))
    return 8


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
    return cast(ColorTransfer, value) if value in allowed else "unknown"


def _coerce_primaries(value: str) -> ColorPrimaries:
    allowed: set[str] = {"bt709", "bt2020", "bt470bg", "smpte170m", "smpte432", "unknown"}
    return cast(ColorPrimaries, value) if value in allowed else "unknown"


def _coerce_space(value: str) -> ColorSpace:
    allowed: set[str] = {"bt709", "bt2020nc", "bt2020c", "bt470bg", "smpte170m", "unknown"}
    return cast(ColorSpace, value) if value in allowed else "unknown"


def _coerce_range(value: str) -> ColorRange:
    allowed: set[str] = {"tv", "pc", "unknown"}
    return cast(ColorRange, value) if value in allowed else "unknown"


def _normalize_container(format_name: str, *, path: Path | None = None) -> str:
    """ffprobe returns comma-joined list like 'mov,mp4,m4a,3gp,...'. Pick first known."""
    if not format_name:
        return "unknown"
    parts = [p.strip() for p in format_name.split(",")]
    # QuickTime MOV and MP4 share one FFprobe demuxer identifier; retain the
    # user-visible container identity from the unambiguous file suffix.
    if "mov" in parts and "mp4" in parts and path is not None:
        if path.suffix.lower() == ".mov":
            return "mov"
        if path.suffix.lower() in {".mp4", ".m4v", ".m4a"}:
            return "mp4"
    for known in ("mp4", "mov", "mkv", "matroska", "webm", "avi"):
        if known in parts:
            return "mkv" if known == "matroska" else known
    return parts[0] if parts else "unknown"
