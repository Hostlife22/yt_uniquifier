"""Pre-flight checks against YouTube target matrix + HDR sanity."""

from __future__ import annotations

import hashlib as _hashlib
import logging as _logging
import threading as _threading
from typing import Literal

from pydantic import BaseModel

from yt_uniquifier.core.models import EncoderCandidate, Plan, SourceMeta

_log = _logging.getLogger(__name__)

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
    findings.extend(_check_pitch_rubberband(plan))
    findings.extend(_check_rubberband_perf(plan, source))
    # Tonemap-order check runs unconditionally — an SDR source with a
    # mis-placed video.tonemap_sdr in the profile would otherwise get
    # no warning because _check_hdr only fires on HDR sources.
    findings.extend(_check_tonemap_order(plan))
    findings.extend(_check_tonemap_sdr_input(plan, source))
    findings.extend(_check_blend_b_input(plan))
    findings.extend(_check_subtitle_burnin(plan))
    return findings


def _check_tonemap_sdr_input(
    plan: Plan, source: SourceMeta
) -> list[PreflightFinding]:
    """Reject SDR source when profile applies video.tonemap_sdr.

    Tonemap is a PQ/HLG → BT.709 conversion. Applied to an SDR source it
    crashes mid-encode (ffmpeg zscale/tonemap path expects HDR transfer)
    with a cryptic "Could not open encoder before EOF" message. This
    check turns that into a clear preflight FAIL so users learn at the
    start of the run instead of after first segment.
    """
    if not source.video:
        return []
    tonemap_present = any(
        tc.enabled and tc.id == "video.tonemap_sdr"
        for tc in plan.profile.transforms
    )
    if not tonemap_present:
        return []
    v = source.video[0]
    if v.color.is_hdr:
        return []
    return [PreflightFinding(
        code="tonemap.sdr_input", severity="fail",
        message=(
            f"Profile applies video.tonemap_sdr but source is SDR "
            f"(transfer={v.color.transfer!r}). Tonemap is only valid "
            f"for HDR (PQ / HLG) sources."
        ),
        suggestion=(
            "Use a non-HDR-to-SDR profile (soft, medium, aggressive, "
            "cid_aware, cid_aggressive) for SDR inputs."
        ),
    )]


def _check_blend_b_input(plan: Plan) -> list[PreflightFinding]:
    """Verify the `b_video_path` for video.blend_b exists.

    Without this, a missing/typo'd path is only discovered when ffmpeg
    fails inside the first segment encode — minutes into a multi-hour
    run.
    """
    from pathlib import Path as _Path
    out: list[PreflightFinding] = []
    for tc in plan.profile.transforms:
        if not tc.enabled or tc.id != "video.blend_b":
            continue
        params = tc.params or {}
        b_path = params.get("b_video_path")
        if not b_path:
            out.append(PreflightFinding(
                code="blend_b.path.missing", severity="fail",
                message="video.blend_b is enabled but b_video_path is empty.",
                suggestion="Set b_video_path to a real file, or disable video.blend_b.",
            ))
            continue
        if not _Path(str(b_path)).exists():
            out.append(PreflightFinding(
                code="blend_b.path.not_found", severity="fail",
                message=f"video.blend_b.b_video_path does not exist: {b_path}",
                suggestion="Fix the path, or disable video.blend_b in the profile.",
            ))
    return out


def _check_subtitle_burnin(plan: Plan) -> list[PreflightFinding]:
    """Verify the burn-in subtitle file exists for every enabled video.subtitles.

    v0.9.0 R2 / F14 — the transform itself is pure (no I/O at build
    time); the SRT must be on disk before run_full starts so a missing
    or mistyped path is caught at preflight rather than mid-encode.
    Use `yt-uniq subtitles generate` to produce one via whisper.cpp.
    """
    from pathlib import Path as _Path
    out: list[PreflightFinding] = []
    for tc in plan.profile.transforms:
        if not tc.enabled or tc.id != "video.subtitles":
            continue
        params = tc.params or {}
        sub_path = params.get("subtitle_path")
        if not sub_path:
            out.append(PreflightFinding(
                code="subtitles.path.missing", severity="fail",
                message="video.subtitles is enabled but subtitle_path is empty.",
                suggestion=(
                    "Set subtitle_path to an existing SRT/ASS file, or run "
                    "`yt-uniq subtitles generate <source>` to auto-create one."
                ),
            ))
            continue
        p = _Path(str(sub_path))
        if not p.exists():
            out.append(PreflightFinding(
                code="subtitles.path.not_found", severity="fail",
                message=f"video.subtitles.subtitle_path does not exist: {sub_path}",
                suggestion=(
                    "Fix the path, or run `yt-uniq subtitles generate "
                    "<source>` to produce it."
                ),
            ))
            continue
        # Light-touch extension check. ffmpeg's ``subtitles`` filter
        # accepts srt/ass/ssa/sbv/vtt; anything else is a hard fail.
        if p.suffix.lower() not in {".srt", ".ass", ".ssa", ".sbv", ".vtt"}:
            out.append(PreflightFinding(
                code="subtitles.path.bad_extension", severity="fail",
                message=(
                    f"video.subtitles.subtitle_path has an unsupported "
                    f"extension: {p.suffix!r}"
                ),
                suggestion="Use .srt, .ass, .ssa, .sbv, or .vtt.",
            ))
    return out


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


def _check_pitch_rubberband(plan: Plan) -> list[PreflightFinding]:
    """Verify ffmpeg has the `rubberband` filter when a profile asks for it."""
    needs_rb = any(
        tc.enabled and tc.id == "audio.pitch_tempo"
        and (tc.params or {}).get("method") == "rubberband"
        for tc in plan.profile.transforms
    )
    if not needs_rb:
        return []
    # Dry-run rather than text-parse: the matrix sweep on 2026-05-31
    # caught a case where `_ffmpeg_has_filter("rubberband")` returned
    # True but ffmpeg failed to open the filter at runtime (18 min into
    # an encode). The dry-run can only false-negative on transient
    # subprocess errors, never false-positive.
    if _ffmpeg_filter_works("rubberband=pitch=1.0", "audio"):
        return []
    return [PreflightFinding(
        code="audio.pitch.rubberband.missing", severity="fail",
        message=(
            "Profile asks for audio.pitch_tempo method='rubberband' but ffmpeg "
            "lacks the `rubberband` filter."
        ),
        suggestion=(
            "Use ffmpeg built with --enable-librubberband (Homebrew default), or "
            "switch the profile to method='asetrate'."
        ),
    )]


_RUBBERBAND_SLOW_DURATION_SEC = 60.0
_RUBBERBAND_SLOW_HEIGHT_PX = 1080


def _check_rubberband_perf(
    plan: Plan, source: SourceMeta
) -> list[PreflightFinding]:
    """Warn that rubberband on long / hi-res sources runs ~10-15× wall time.

    The 2026-05-31 real-video matrix (`docs/bug-triage-2026-05-31.md` §9)
    timed out 4 cells — `{cid_aware, cid_aggressive} × {synth_sdr_4k,
    synth_long_5min}` — all hitting the 1800s ceiling. Not a bug: the
    `rubberband` filter is single-threaded inside ffmpeg and runs the
    audio chain in serial with the (now-parallel) video chain, so on
    >60s or >1080p sources the rubberband pass dominates wall time.

    Emitted at WARN severity so the encode still proceeds. Users who
    accept the wall cost (formant preservation for voice — Smitelli
    2010 ±5% CID match boundary) can ignore the message; users on
    throughput-sensitive batches get pointed at `method='asetrate'`.
    """
    if not source.video:
        return []
    needs_rb = any(
        tc.enabled and tc.id == "audio.pitch_tempo"
        and (tc.params or {}).get("method") == "rubberband"
        for tc in plan.profile.transforms
    )
    if not needs_rb:
        return []
    v = source.video[0]
    long_source = source.duration_sec > _RUBBERBAND_SLOW_DURATION_SEC
    hi_res_source = v.height > _RUBBERBAND_SLOW_HEIGHT_PX
    if not (long_source or hi_res_source):
        return []
    triggers = []
    if long_source:
        triggers.append(
            f"duration {source.duration_sec:.0f}s "
            f"> {_RUBBERBAND_SLOW_DURATION_SEC:.0f}s"
        )
    if hi_res_source:
        triggers.append(
            f"height {v.height}p > {_RUBBERBAND_SLOW_HEIGHT_PX}p"
        )
    return [PreflightFinding(
        code="audio.pitch.rubberband.slow", severity="warn",
        message=(
            f"Profile uses audio.pitch_tempo method='rubberband' on a "
            f"{' and '.join(triggers)} source. Rubberband runs ~10-15× "
            f"wall time vs 'asetrate' on long / >=4K content "
            f"(see docs/profiles.md#rubberband-performance-characteristic)."
        ),
        suggestion=(
            "For throughput, switch the profile to "
            "audio.pitch_tempo.method='asetrate'. Keep 'rubberband' "
            "when formant preservation matters more than wall time."
        ),
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
        # video.tonemap_sdr generates a `zscale=transfer=linear → tonemap
        # → zscale=transfer=bt709` chain (see core/transforms/video_tonemap.py).
        # If ffmpeg lacks zscale, the run crashes mid-encode with
        # "No such filter: zscale" at the first segment — fail-fast at
        # preflight instead. Real-video matrix re-run on 2026-05-31
        # caught this on the synth_hdr10 × cid_aware_hdr_to_sdr cell;
        # the OK-only path slipped past a zimg-missing ffmpeg build.
        if not _ffmpeg_filter_works(
            "zscale=tin=bt709:min=bt709:pin=bt709:t=bt709:m=bt709:p=bt709", "video"
        ):
            findings.append(PreflightFinding(
                code="tonemap.zscale.missing", severity="fail",
                message=(
                    "Profile applies video.tonemap_sdr, which depends on "
                    "the `zscale` filter (zimg). ffmpeg on this system "
                    "lacks zscale, so tonemap would fail mid-encode."
                ),
                suggestion=(
                    "Install ffmpeg built with --enable-libzimg "
                    "(Homebrew default does), or pick a profile without "
                    "video.tonemap_sdr."
                ),
            ))
            return findings
        findings.append(PreflightFinding(
            code="hdr.tonemap.ok", severity="ok",
            message=(
                f"HDR source ({v.color.transfer}) will be tonemapped to BT.709 SDR."
            ),
        ))
        # Tonemap-order check is now hoisted to preflight() so it fires
        # for SDR sources too — no need to duplicate it here.
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
    # Dry-run probe rather than text-parse for the same reason as
    # rubberband (see _check_pitch_rubberband). zscale=t=bt709 is a
    # benign no-op color transfer that any working zimg build accepts.
    _zscale_probe = "zscale=tin=bt709:min=bt709:pin=bt709:t=bt709:m=bt709:p=bt709"
    if not _ffmpeg_filter_works(_zscale_probe, "video"):
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


# Keyed on a hash of `ffmpeg -version` so an out-of-process binary
# upgrade (Homebrew bump, container rebuild) invalidates the entry on
# the next call instead of returning stale availability data.
_FFMPEG_FILTERS_CACHE: dict[str, set[str]] = {}
_FFMPEG_FILTERS_CACHE_LOCK = _threading.Lock()

# Separate cache for the dry-run prober — keys are
# (version_key, kind, probe_spec) so the same ffmpeg can probe many
# different filter specs without trashing one another.
_FFMPEG_FILTER_WORKS_CACHE: dict[tuple[str, str, str], bool] = {}
_FFMPEG_FILTER_WORKS_CACHE_LOCK = _threading.Lock()


def _ffmpeg_filter_works(
    probe_spec: str, kind: Literal["audio", "video"]
) -> bool:
    """Verify a filter is usable by actually opening it on a 0.001s lavfi source.

    Defense-in-depth replacement for `_ffmpeg_has_filter`. The text-parse
    of ``ffmpeg -filters`` has an intermittent false-positive failure
    mode (observed during the 2026-05-31 real-video matrix run: cache
    reported ``rubberband`` present, runtime ffmpeg threw "No such
    filter" 18 minutes into the encode). Spending ~200ms on a real
    filter-graph open at preflight time removes the false-positive
    class entirely — if ffmpeg can open ``-af rubberband=pitch=1.0``
    against a 1ms sine source, it can open the same filter against the
    user's content; if it cannot, we fail at second zero instead of
    minute 18.

    ``probe_spec`` is the full ffmpeg filter spec, e.g.
    ``"rubberband=pitch=1.0"`` or ``"zscale=t=bt709:m=bt709"``. We pass
    a benign default value so the probe doesn't need filter-specific
    knowledge of valid parameters.

    Cached on (version_key, kind, probe_spec) so repeated callers in
    the same process don't re-spawn ffmpeg; cache invalidates on any
    ``ffmpeg -version`` change (homebrew bump, container rebuild).
    """
    import subprocess

    from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

    bin_path = ffmpeg_bin()
    version_key = _ffmpeg_version_key(bin_path)
    cache_key = (version_key, kind, probe_spec)
    with _FFMPEG_FILTER_WORKS_CACHE_LOCK:
        if cache_key in _FFMPEG_FILTER_WORKS_CACHE:
            return _FFMPEG_FILTER_WORKS_CACHE[cache_key]

        if kind == "audio":
            input_args = [
                "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000",
            ]
            filter_args = ["-af", probe_spec]
        else:
            input_args = [
                "-f", "lavfi", "-i",
                "testsrc2=size=64x64:rate=1",
            ]
            filter_args = ["-vf", probe_spec]

        try:
            proc = subprocess.run(
                [bin_path, "-hide_banner", "-loglevel", "error",
                 *input_args, *filter_args,
                 "-t", "0.001", "-f", "null", "-"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            works = proc.returncode == 0
            if not works:
                _log.debug(
                    "ffmpeg filter dry-run %r (%s) failed exit=%s: %s",
                    probe_spec, kind, proc.returncode,
                    (proc.stderr or "").strip()[-200:],
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            _log.warning(
                "ffmpeg filter dry-run %r (%s) errored: %s",
                probe_spec, kind, exc,
            )
            works = False

        _FFMPEG_FILTER_WORKS_CACHE[cache_key] = works
        return works


def _ffmpeg_version_key(ffmpeg_path: str) -> str:
    """Stable digest of `ffmpeg -version` output for cache invalidation.

    Used as the cache key in `_ffmpeg_has_filter` so swapping the ffmpeg
    binary forces a fresh probe instead of returning stale filter sets.
    Returns the literal string ``"unknown"`` when the version probe
    fails — better to merge into one bucket than crash preflight on a
    transient ffmpeg startup failure.
    """
    import subprocess

    try:
        out = subprocess.check_output(
            [ffmpeg_path, "-hide_banner", "-version"],
            text=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    return _hashlib.sha256(out.encode("utf-8")).hexdigest()[:16]


def _ffmpeg_has_filter(name: str) -> bool:
    """Cached check whether ffmpeg has a given filter (e.g. zscale).

    Cache key includes a hash of ``ffmpeg -version`` so an out-of-process
    binary upgrade invalidates the entry. Lock-guarded — under parallel
    batch / GUI workers two callers could otherwise both miss the cache
    and launch redundant ``ffmpeg -filters`` subprocesses; on
    free-threaded CPython 3.13+ the dict assignment race is real, not
    just wasted work.

    A real ffmpeg failure (binary missing, hung, version mismatch) is
    logged via ``logging.getLogger(__name__).warning`` before returning
    False so the user sees the cause in CI logs instead of a silent
    "feature unavailable".
    """
    import subprocess

    from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

    bin_path = ffmpeg_bin()
    version_key = _ffmpeg_version_key(bin_path)
    with _FFMPEG_FILTERS_CACHE_LOCK:
        if version_key not in _FFMPEG_FILTERS_CACHE:
            try:
                # check=False so we can inspect returncode + stderr; the
                # previous check=True swallowed the actual error text
                # into an empty CalledProcessError handler.
                proc = subprocess.run(
                    [bin_path, "-hide_banner", "-filters"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                _log.warning("ffmpeg -filters probe failed: %s", exc)
                _FFMPEG_FILTERS_CACHE[version_key] = set()
                return False
            if proc.returncode != 0:
                _log.warning(
                    "ffmpeg -filters exited %s: %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout or "").strip()[-300:],
                )
                _FFMPEG_FILTERS_CACHE[version_key] = set()
                return False
            # Word-split match: each ffmpeg filter listing line has the
            # filter name as the second whitespace token. A plain
            # `name in proc.stdout` substring fallback would mis-match
            # `eq` inside `equalizer`, so we don't use it.
            names: set[str] = set()
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    names.add(parts[1])
            _FFMPEG_FILTERS_CACHE[version_key] = names
        return name in _FFMPEG_FILTERS_CACHE[version_key]


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
