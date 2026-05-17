"""Pre-flight checks against YouTube target matrix + HDR sanity."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from yt_uniquifier.core.models import EncoderCandidate, Plan, SourceMeta

Severity = Literal["ok", "warn", "fail"]


class PreflightFinding(BaseModel):
    code: str
    severity: Severity
    message: str
    suggestion: str | None = None


# YouTube recommended targets (https://support.google.com/youtube/answer/4603579)
_ALLOWED_CONTAINERS = {"mp4", "mov", "mkv"}
_ALLOWED_VIDEO_CODECS = {"h264", "hevc", "vp9", "av1"}
_ALLOWED_AUDIO_CODECS = {"aac", "opus", "mp3"}
_PREFERRED_FPS = (23.976, 24, 25, 29.97, 30, 50, 59.94, 60)
_ALLOWED_AUDIO_SR = {44100, 48000}
_LOUDNORM_TARGET = -14.0
_COLOR_TRANSFORMS = {"video.color_eq", "video.noise"}


def preflight(
    source: SourceMeta, plan: Plan, encoder: EncoderCandidate
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []

    findings.append(_check_container(source))
    findings.append(_check_video_codec(source))
    findings.extend(_check_audio_streams(source))
    findings.extend(_check_fps(source))
    findings.extend(_check_hdr(source, plan, encoder))
    findings.extend(_check_subtitles(source))
    findings.extend(_check_loudnorm(plan))
    findings.extend(_check_bitrate(source))
    return findings


def has_fail(findings: list[PreflightFinding]) -> bool:
    return any(f.severity == "fail" for f in findings)


def _check_container(source: SourceMeta) -> PreflightFinding:
    if source.container in _ALLOWED_CONTAINERS:
        return PreflightFinding(
            code="container.ok", severity="ok",
            message=f"Container {source.container!r} is acceptable.",
        )
    return PreflightFinding(
        code="container.unsupported", severity="warn",
        message=f"Container {source.container!r} is not in YouTube's preferred list.",
        suggestion="Output is mp4 by default, so this is informational only.",
    )


def _check_video_codec(source: SourceMeta) -> PreflightFinding:
    if not source.video:
        return PreflightFinding(
            code="video.missing", severity="fail",
            message="No video stream found.",
            suggestion="This tool requires a video stream; cannot proceed.",
        )
    v = source.video[0]
    if v.codec in _ALLOWED_VIDEO_CODECS:
        return PreflightFinding(
            code="video.codec.ok", severity="ok",
            message=f"Source video codec {v.codec!r} is acceptable.",
        )
    return PreflightFinding(
        code="video.codec.unusual", severity="warn",
        message=f"Source codec {v.codec!r} is unusual but will be re-encoded.",
    )


def _check_audio_streams(source: SourceMeta) -> list[PreflightFinding]:
    if not source.audio:
        return [
            PreflightFinding(
                code="audio.missing", severity="warn",
                message="No audio stream found.",
                suggestion="Output will be video-only; verify this is intended.",
            )
        ]
    out: list[PreflightFinding] = []
    a = source.audio[0]
    if a.codec not in _ALLOWED_AUDIO_CODECS:
        out.append(PreflightFinding(
            code="audio.codec.unusual", severity="warn",
            message=f"Audio codec {a.codec!r} unusual; will be re-encoded to AAC.",
        ))
    if a.sample_rate not in _ALLOWED_AUDIO_SR:
        out.append(PreflightFinding(
            code="audio.sr.bad", severity="warn",
            message=f"Sample rate {a.sample_rate}Hz is not 44.1k/48k; resample recommended.",
            suggestion="YouTube will resample the upload, which can reduce quality.",
        ))
    return out


def _check_fps(source: SourceMeta) -> list[PreflightFinding]:
    if not source.video:
        return []
    v = source.video[0]
    if any(abs(v.fps - target) <= 0.1 for target in _PREFERRED_FPS):
        return [PreflightFinding(
            code="fps.ok", severity="ok",
            message=f"FPS {v.fps:.3f} matches YouTube's preferred set.",
        )]
    return [PreflightFinding(
        code="fps.unusual", severity="warn",
        message=f"FPS {v.fps:.3f} is non-standard; playback may be smoothed by YouTube.",
        suggestion="Consider re-encoding to 24/25/30/50/60 fps.",
    )]


def _check_tonemap_order(plan: Plan) -> list[PreflightFinding]:
    """Warn if video.tonemap_sdr is not the first enabled transform.

    Other color/eq/noise ops applied BEFORE tonemap operate on PQ-encoded
    values (which are nonlinear with light), giving wrong colors. Placing
    tonemap first means everything after sees plain BT.709 SDR.
    """
    enabled = [tc for tc in plan.profile.transforms if tc.enabled]
    for i, tc in enumerate(enabled):
        if tc.id == "video.tonemap_sdr" and i != 0:
            return [PreflightFinding(
                code="tonemap.not_first", severity="warn",
                message=(
                    "video.tonemap_sdr should be the first enabled transform "
                    f"(currently position {i + 1} of {len(enabled)})."
                ),
                suggestion="Move video.tonemap_sdr to the top of profile.transforms.",
            )]
    return []


_HDR_CAPABLE_ENCODERS = {
    "libx265",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_amf",
    "hevc_videotoolbox",
}


def _check_hdr(
    source: SourceMeta, plan: Plan, encoder: EncoderCandidate
) -> list[PreflightFinding]:
    if not source.video:
        return []
    v = source.video[0]
    if not v.color.is_hdr:
        return []
    findings: list[PreflightFinding] = []

    # Tonemap path: source HDR will be collapsed into BT.709 SDR explicitly.
    # Supersedes both the color-transforms restriction and the encoder bit-depth
    # restriction (both apply only when we're trying to KEEP HDR).
    tonemap_present = any(
        tc.enabled and tc.id == "video.tonemap_sdr" for tc in plan.profile.transforms
    )
    if tonemap_present:
        findings.append(PreflightFinding(
            code="hdr.tonemap.ok", severity="ok",
            message=(
                f"HDR source ({v.color.transfer}) will be tonemapped to BT.709 SDR."
            ),
        ))
        findings.extend(_check_tonemap_order(plan))
        return findings

    if not plan.profile.keep_hdr:
        offenders = [
            tc.id for tc in plan.profile.transforms
            if tc.enabled and tc.id in _COLOR_TRANSFORMS
        ]
        if offenders:
            findings.append(PreflightFinding(
                code="hdr.color.transforms", severity="fail",
                message=(
                    f"Source is HDR ({v.color.transfer}) but profile applies color "
                    f"transforms {offenders} without --keep-hdr. Output color will be wrong."
                ),
                suggestion=(
                    "Set keep_hdr: true, add video.tonemap_sdr first, or "
                    "remove the color transforms."
                ),
            ))
        # Without keep_hdr we still re-encode to 8-bit yuv420p, which collapses HDR.
        return findings

    # keep_hdr=True path: need zscale (zimg) + 10-bit-capable encoder.
    if not _ffmpeg_has_filter("zscale"):
        findings.append(PreflightFinding(
            code="hdr.zscale.missing", severity="fail",
            message=(
                "ffmpeg lacks the `zscale` filter (zimg) required to keep HDR "
                "through color transforms."
            ),
            suggestion="Install ffmpeg built with --enable-libzimg (Homebrew default does).",
        ))
    if encoder.name not in _HDR_CAPABLE_ENCODERS:
        findings.append(PreflightFinding(
            code="hdr.encoder.8bit", severity="fail",
            message=f"Encoder {encoder.name!r} cannot output 10-bit HDR; result will be SDR.",
            suggestion=(
                "Use --encoder libx265, hevc_nvenc, hevc_qsv, hevc_amf, or "
                "hevc_videotoolbox."
            ),
        ))
    # blend_b is technically a color op but its filter_str can't be wrapped — warn.
    if any(tc.id == "video.blend_b" and tc.enabled for tc in plan.profile.transforms):
        findings.append(PreflightFinding(
            code="hdr.blend.unwrapped", severity="warn",
            message=(
                "video.blend_b runs in the source's transfer domain even with keep_hdr — "
                "blend math may shift HDR colors."
            ),
            suggestion="Disable blend_b for HDR sources, or accept the colour shift.",
        ))
    return findings


_FFMPEG_FILTERS_CACHE: dict[str, set[str]] = {}


def _ffmpeg_has_filter(name: str) -> bool:
    """Cached check whether ffmpeg has a given filter (e.g. zscale)."""
    import subprocess

    from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

    key = "filters"
    if key not in _FFMPEG_FILTERS_CACHE:
        try:
            proc = subprocess.run(
                [ffmpeg_bin(), "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=10, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            _FFMPEG_FILTERS_CACHE[key] = set()
            return False
        # Substring match on the filters listing is enough for our needs
        # (filter names are space-delimited words; false positives unlikely).
        names: set[str] = set()
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                names.add(parts[1])
        if name in proc.stdout:
            names.add(name)
        _FFMPEG_FILTERS_CACHE[key] = names
    return name in _FFMPEG_FILTERS_CACHE[key]


def _check_subtitles(source: SourceMeta) -> list[PreflightFinding]:
    image_based = [s for s in source.subtitle if s.is_image_based]
    if not image_based:
        return []
    return [PreflightFinding(
        code="subs.image_based", severity="warn",
        message=f"{len(image_based)} image-based subtitle stream(s) (pgs/dvb) will be skipped.",
        suggestion="Convert to srt/ass before processing if you need to preserve them.",
    )]


def _check_loudnorm(plan: Plan) -> list[PreflightFinding]:
    has_loudnorm = any(
        tc.enabled and tc.id == "audio.loudnorm" for tc in plan.profile.transforms
    )
    if has_loudnorm:
        return [PreflightFinding(
            code="loudnorm.ok", severity="ok",
            message=f"Loudness will be normalized to {_LOUDNORM_TARGET} LUFS.",
        )]
    return [PreflightFinding(
        code="loudnorm.missing", severity="warn",
        message=(
            "Profile lacks audio.loudnorm; output may not hit YouTube's "
            f"{_LOUDNORM_TARGET} LUFS target."
        ),
        suggestion="Add audio.loudnorm to the profile transforms list.",
    )]


def _check_bitrate(source: SourceMeta) -> list[PreflightFinding]:
    if not source.video:
        return []
    v = source.video[0]
    if v.bit_rate is None:
        return []
    height = v.height
    # Rough bracket upper-bounds for h264 (https://support.google.com/youtube/answer/1722171).
    if height <= 1080:
        ceiling = 12_000_000
    elif height <= 1440:
        ceiling = 24_000_000
    else:
        ceiling = 68_000_000
    projected_max = int(v.bit_rate * 1.25)
    if projected_max > ceiling:
        return [PreflightFinding(
            code="bitrate.over", severity="warn",
            message=(
                f"Projected output bitrate ~{projected_max // 1_000_000} Mbps exceeds "
                f"YouTube's h264 ceiling for {height}p (~{ceiling // 1_000_000} Mbps)."
            ),
        )]
    return []
