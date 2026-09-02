"""Pre-flight checks against YouTube target matrix + HDR sanity."""

from __future__ import annotations

import hashlib as _hashlib
import logging as _logging
import shutil as _shutil
import threading as _threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from yt_uniquifier.core.models import EncoderCandidate, Plan, SourceMeta
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices

_log = _logging.getLogger(__name__)

Severity = Literal["ok", "info", "warn", "fail"]


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
    source: SourceMeta,
    plan: Plan,
    encoder: EncoderCandidate,
    *,
    work_dir: Path | None = None,
    accept_watermark_risk: bool = False,
    verify_encoder_capability: bool = False,
) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []

    # v1.3.0 Task 31 — DRM guardrail.  Runs FIRST, before every other
    # check, because an encrypted source is unsupported regardless of
    # codec / profile / encoder — no other finding adds information.
    # No override exists; encrypted content is by definition not the
    # operator's to re-encode (see docs/architecture.md § ethics).
    findings.extend(_check_input_drm(source))
    # v1.3.0 Task 30 — watermark guardrail.  Runs before YouTube-target
    # checks so the operator confronts ownership/licensing attestation
    # before any time is spent on profile fit.  Returns an empty list
    # when OpenCV isn't installed (the guardrail then becomes opt-in
    # via the [scene] extra).
    findings.extend(_check_input_watermark(
        source, plan, accept_watermark_risk=accept_watermark_risk,
    ))
    # v1.0.1: disk-space check runs before encoder/bitrate so a thin-margin
    # warning sits at the top of the report. Skipped when work_dir is None
    # (callers that build a Plan without committing to a run dir — e.g.
    # the GUI calibration screen — don't need the check).
    if work_dir is not None:
        findings.extend(_check_disk_space(source, work_dir))
    findings.append(_check_container(source))
    findings.append(_check_video_codec(source))
    findings.extend(_check_audio_streams(source))
    findings.extend(_check_fps(source))
    findings.extend(_check_hdr(source, plan, encoder))
    findings.extend(_check_subtitles(source, plan))
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
    findings.extend(_check_timeline_rate(plan))
    findings.extend(_check_audio_channel_layout(plan))
    findings.extend(_check_container_metadata_loss(plan))
    if verify_encoder_capability and source.video:
        findings.extend(_check_encoder_capability(plan))
    return findings


def _check_container_metadata_loss(plan: Plan) -> list[PreflightFinding]:
    """Report source dispositions the selected container cannot represent."""
    if plan.profile.output_container not in {"mp4", "mov"}:
        return []
    supported = (
        {"default"}
        if plan.profile.output_container == "mov"
        else {"default", "forced", "hearing_impaired", "visual_impaired"}
    )
    lost: set[str] = set()
    for index in selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    ):
        audio_stream = plan.source.audio[index]
        lost.update(set(audio_stream.dispositions) - supported)
    for subtitle_stream in plan.source.subtitle:
        lost.update(set(subtitle_stream.dispositions) - supported)
    if not lost:
        return []
    names = ", ".join(sorted(lost))
    return [PreflightFinding(
        code="metadata.disposition.container_loss",
        severity="warn",
        message=(
            f"The {plan.profile.output_container.upper()} container cannot "
            f"represent source stream disposition(s): {names}."
        ),
        suggestion="Use an MKV output profile to preserve the complete disposition set.",
    )]


def _check_encoder_capability(plan: Plan) -> list[PreflightFinding]:
    """Run the selected encoder with this Plan's resolution/format/RC."""
    from yt_uniquifier.core.encoder import probe_encoder_for_plan

    result = probe_encoder_for_plan(plan)
    detail = f"{result.width}x{result.height} {result.pix_fmt}"
    if result.supported:
        return [PreflightFinding(
            code="encoder.capability.ok",
            severity="ok",
            message=f"Encoder {plan.encoder.name!r} passed job probe ({detail}).",
        )]
    return [PreflightFinding(
        code="encoder.capability.unsupported",
        severity="fail",
        message=(
            f"Encoder {plan.encoder.name!r} failed the job-specific probe "
            f"({detail}): {result.error or 'unknown ffmpeg error'}"
        ),
        suggestion=(
            "Select another encoder or reduce resolution/bit depth; the run "
            "was stopped before any segments were created."
        ),
    )]


def _check_audio_channel_layout(plan: Plan) -> list[PreflightFinding]:
    """Reject transforms whose filter topology is stereo-only."""
    has_haas = any(
        transform.enabled and transform.id == "audio.haas_stereo"
        for transform in plan.profile.transforms
    )
    if not has_haas:
        return []

    selected_audio = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    if not selected_audio:
        return []
    main_audio = plan.source.audio[selected_audio[0]]
    if main_audio.channels == 2:
        return []
    return [PreflightFinding(
        code="audio.haas_requires_stereo",
        severity="fail",
        message=(
            "audio.haas_stereo requires a two-channel main audio stream; "
            f"the selected stream has {main_audio.channels} channel(s)."
        ),
        suggestion=(
            "Disable audio.haas_stereo or explicitly prepare a stereo mix "
            "before processing. Automatic downmixing is intentionally avoided."
        ),
    )]


def _check_timeline_rate(plan: Plan) -> list[PreflightFinding]:
    """Reject independent video/audio rate changes that silently cut content."""
    video_rate = 1.0
    audio_rate = 1.0
    for transform in plan.profile.transforms:
        if not transform.enabled:
            continue
        if transform.id == "video.speed":
            raw_rate = transform.params.get("rate", 1.0)
            if isinstance(raw_rate, (int, float)):
                video_rate *= float(raw_rate)
        elif transform.id == "audio.pitch_tempo":
            raw_tempo = transform.params.get("tempo", 1.0)
            if isinstance(raw_tempo, (int, float)):
                audio_rate *= float(raw_tempo)

    selected_audio = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    if selected_audio and abs(video_rate - audio_rate) > 1e-6:
        return [PreflightFinding(
            code="timeline.rate_mismatch", severity="fail",
            message=(
                f"Video playback rate ({video_rate:g}) and selected main-audio "
                f"tempo ({audio_rate:g}) differ; output would be out of sync."
            ),
            suggestion=(
                "Set video.speed.rate and audio.pitch_tempo.tempo to the same "
                "value, or remove both rate changes."
            ),
        )]
    if len(selected_audio) > 1 and abs(video_rate - 1.0) > 1e-6:
        return [PreflightFinding(
            code="timeline.passthrough_audio_rate", severity="fail",
            message=(
                "Playback-rate changes cannot preserve additional stream-copy "
                "audio tracks in sync."
            ),
            suggestion="Use audio_tracks: first, or remove the playback-rate change.",
        )]
    if abs(video_rate - 1.0) > 1e-6 and (
        plan.source.subtitle or plan.source.chapters
    ):
        return [PreflightFinding(
            code="timeline.aux_stream_rate", severity="fail",
            message=(
                "Playback-rate changes require retiming subtitle and chapter "
                "timestamps, which is not yet supported safely."
            ),
            suggestion=(
                "Remove video.speed for this source, or remove/retime subtitles "
                "and chapters before processing."
            ),
        )]
    return []


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


# v1.0.1: filter-graph overhead + per-segment header padding empirically
# adds ~25-35% to the raw bitrate-times-duration estimate on 1080p
# libx264 runs. 1.3× is the safe upper end; the actual rule the run
# stays within is the .1× warning margin below.
_DISK_BYTES_OVERHEAD_FACTOR = 1.3
_DISK_FREE_FAIL_FACTOR = 1.1
_DISK_FREE_WARN_FACTOR = 1.5
_DEFAULT_TARGET_BITRATE_BPS = 8_000_000


def _check_disk_space(
    source: SourceMeta, work_dir: Path,
) -> list[PreflightFinding]:
    """v1.0.1: refuse runs that almost certainly won't fit on disk.

    Estimate ``estimated_bytes ≈ source.duration_sec × target_bitrate ×
    1.3`` where ``target_bitrate`` is the source's declared video
    bitrate (falling back to 8 Mbps when missing). Compare against
    ``shutil.disk_usage(work_dir).free``:

      * ``error`` (severity ``fail``) when ``free < estimated × 1.1`` —
        ffmpeg would fill the disk mid-segment and leave a half-encoded
        state.json that is harder to recover from than refusing up
        front;
      * ``warn`` when ``free < estimated × 1.5`` — the run probably fits
        but the safety margin for swap, system updates, and other
        processes is thin enough that the user should be told.

    The check is skipped when ``work_dir`` doesn't exist yet — the
    orchestrator creates it after preflight runs, so callers driving
    a fresh dir should resolve disk_usage against the nearest existing
    parent.
    """
    # Walk up to the nearest existing ancestor so the check can run
    # before the orchestrator's ``options.work_dir.mkdir`` call.
    probe_dir: Path | None = work_dir
    while probe_dir is not None and not probe_dir.exists():
        probe_dir = probe_dir.parent if probe_dir.parent != probe_dir else None
    if probe_dir is None:
        # Couldn't resolve any existing parent (Path("/nonexistent")
        # on a system without root? — treat as informational, not a
        # blocking error).
        return [PreflightFinding(
            code="disk.unknown",
            severity="warn",
            message=(
                f"Could not resolve a real parent for {work_dir} — "
                "skipping disk-space check."
            ),
            suggestion=(
                "Pass --work-dir to a directory that exists or whose "
                "parent does."
            ),
        )]

    bitrate = _DEFAULT_TARGET_BITRATE_BPS
    if source.video and source.video[0].bit_rate:
        # ffprobe bit_rate is in bits/sec.
        bitrate = source.video[0].bit_rate
    estimated = int(
        max(0.0, source.duration_sec)
        * (bitrate / 8.0)
        * _DISK_BYTES_OVERHEAD_FACTOR,
    )
    try:
        usage = _shutil.disk_usage(probe_dir)
    except OSError as exc:
        return [PreflightFinding(
            code="disk.probe_failed",
            severity="warn",
            message=f"Could not query disk usage for {probe_dir}: {exc}",
            suggestion=(
                "Verify the path is on a mounted filesystem before running."
            ),
        )]

    free = usage.free
    estimated_gib = estimated / (1024 ** 3)
    free_gib = free / (1024 ** 3)
    if free < estimated * _DISK_FREE_FAIL_FACTOR:
        return [PreflightFinding(
            code="disk.space.insufficient",
            severity="fail",
            message=(
                f"Free space on {probe_dir} is {free_gib:.1f} GiB; "
                f"the run is estimated to need ~{estimated_gib:.1f} GiB "
                "(source bitrate × duration × 1.3 overhead). Refusing to "
                "start so a mid-run disk-full failure doesn't leave a "
                "half-encoded state.json."
            ),
            suggestion=(
                "Free at least "
                f"{(estimated * _DISK_FREE_FAIL_FACTOR - free) / (1024**3):.1f}"
                " GiB or point --work-dir at a roomier filesystem."
            ),
        )]
    if free < estimated * _DISK_FREE_WARN_FACTOR:
        return [PreflightFinding(
            code="disk.space.tight",
            severity="warn",
            message=(
                f"Free space on {probe_dir} is {free_gib:.1f} GiB versus "
                f"~{estimated_gib:.1f} GiB estimated — the safety margin "
                "for swap, system updates, and other processes is thin."
            ),
            suggestion="Free a few GiB before long unattended runs.",
        )]
    return [PreflightFinding(
        code="disk.space.ok",
        severity="ok",
        message=(
            f"Free space on {probe_dir}: {free_gib:.1f} GiB "
            f"(estimated need ~{estimated_gib:.1f} GiB)."
        ),
    )]


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

    if v.color.dynamic_metadata:
        metadata_names = ", ".join(v.color.dynamic_metadata)
        findings.append(PreflightFinding(
            code="hdr.dynamic_metadata.unsupported",
            severity="fail",
            message=(
                "Source contains dynamic HDR metadata that cannot be "
                f"preserved safely by this re-encode pipeline: {metadata_names}."
            ),
            suggestion=(
                "Use an SDR tonemap profile, or process with a verified Dolby "
                "Vision/HDR10+ metadata extraction and reinjection workflow."
            ),
        ))
    if (
        (v.color.mastering_display is not None or v.color.max_cll is not None)
        and encoder.name != "libx265"
    ):
        findings.append(PreflightFinding(
            code="hdr.static_metadata.encoder_unverified",
            severity="fail",
            message=(
                f"Encoder {encoder.name!r} has no verified static HDR10 metadata "
                "reinjection path; mastering display/CLL could be lost."
            ),
            suggestion="Use libx265 for this source, or tonemap explicitly to SDR.",
        ))

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


def _check_subtitles(source: SourceMeta, plan: Plan) -> list[PreflightFinding]:
    image_based = [s for s in source.subtitle if s.is_image_based]
    if not image_based:
        return []
    if plan.profile.output_container == "mkv":
        return [PreflightFinding(
            code="subs.image_based", severity="ok",
            message=(
                f"{len(image_based)} image-based subtitle stream(s) will be "
                "preserved in MKV."
            ),
        )]
    return [PreflightFinding(
        code="subs.image_based", severity="fail",
        message=(
            f"{len(image_based)} image-based subtitle stream(s) cannot be muxed "
            f"into {plan.profile.output_container.upper()} without data loss."
        ),
        suggestion="Use MKV output or convert the subtitles to SRT/ASS first.",
    )]


def _check_loudnorm(plan: Plan) -> list[PreflightFinding]:
    has_loudnorm = any(
        tc.enabled and tc.id == "audio.loudnorm" for tc in plan.profile.transforms
    )
    if has_loudnorm:
        return [PreflightFinding(
            code="loudnorm.ok", severity="ok",
            message=(
                f"Loudness will be normalized to "
                f"{plan.profile.target_loudness_lufs:g} LUFS."
            ),
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


def _check_input_watermark(
    source: SourceMeta,
    plan: Plan,
    *,
    accept_watermark_risk: bool = False,
) -> list[PreflightFinding]:
    """v1.3.0 Task 30 — broadcaster watermark / station-ID guardrail.

    Three short-circuits:
      * ``accept_watermark_risk=True`` (operator attested ownership /
        license via CLI flag) — skip with an ``info`` finding so the
        attestation appears in the report for the audit log.
      * Profile-level opt-out (``skip_watermark_check: true``) — same
        skip, different rationale tag.
      * OpenCV unavailable — guardrail short-circuits at the detector;
        we surface an ``info`` finding pointing operators at the
        ``[scene]`` extra.

    Otherwise we sample frames + template-match.  A positive detection
    is severity ``error`` by default so the run aborts unless overridden.
    """
    if accept_watermark_risk:
        return [PreflightFinding(
            code="watermark.attested", severity="info",
            message=(
                "Watermark guardrail skipped per --accept-watermark-risk "
                "(operator attested ownership / licensed use)."
            ),
        )]
    if getattr(plan.profile, "skip_watermark_check", False):
        return [PreflightFinding(
            code="watermark.skipped_by_profile", severity="info",
            message=(
                "Watermark guardrail skipped per profile setting "
                "skip_watermark_check=true."
            ),
        )]
    from yt_uniquifier.core.guardrails.watermark import detect_watermark
    result = detect_watermark(source.path, duration_sec=source.duration_sec)
    if result is None:
        return [PreflightFinding(
            code="watermark.unavailable", severity="info",
            message=(
                "Watermark guardrail skipped — install the [scene] "
                "extra (pip install yt-uniquifier[scene]) to enable."
            ),
        )]
    if not result.detected:
        return []
    return [PreflightFinding(
        code="watermark.detected", severity="fail",
        message=(
            f"Likely broadcaster watermark / station ID detected "
            f"({result.matched_frames}/{result.sampled_frames} sampled "
            f"frames matched; max confidence {result.confidence:.2f}). "
            "yt-uniquifier is for owned/licensed content only."
        ),
        suggestion=(
            "If you own the content or have a fair-use / license to "
            "re-upload, pass --accept-watermark-risk (or set "
            "skip_watermark_check: true in the profile) to attest and "
            "proceed."
        ),
    )]


def _check_input_drm(source: SourceMeta) -> list[PreflightFinding]:
    """v1.3.0 Task 31 — DRM-encrypted-source rejection.

    No override exists.  An encrypted source is by definition not the
    operator's to re-encode; an opt-out would invite abuse.  Operators
    who genuinely own the content must obtain unencrypted copies
    through licensed channels.
    """
    from yt_uniquifier.core.guardrails.drm import detect_drm
    try:
        result = detect_drm(source.path)
    except Exception as exc:  # noqa: BLE001 — defensive against probe errors
        _log.warning("DRM guardrail probe failed: %s", exc)
        return [PreflightFinding(
            code="drm.probe_failed", severity="warn",
            message=f"DRM probe could not run ({exc}); proceeding without check.",
            suggestion="Verify ffprobe is on PATH; re-run preflight.",
        )]
    if result.probe_failed:
        return [PreflightFinding(
            code="drm.probe_failed", severity="warn",
            message=(
                f"DRM probe could not inspect the source ({result.note}). "
                "Proceeding without an encryption verdict."
            ),
            suggestion=(
                "If the file is corrupt or truncated, repair it first; "
                "if it's an encrypted DRM container, the decode will "
                "fail with a different error during the run."
            ),
        )]
    if not result.is_encrypted:
        return []
    detail = (
        f" (marker: {result.matched_marker!r})"
        if result.matched_marker else (f" ({result.note})" if result.note else "")
    )
    return [PreflightFinding(
        code="drm.encrypted", severity="fail",
        message=(
            "Source appears to be DRM-encrypted" + detail + "."
            "  yt-uniquifier refuses to process encrypted content."
        ),
        suggestion=(
            "Obtain an unencrypted, licensed copy of the content through "
            "the rights-holder's official distribution channel."
        ),
    )]
