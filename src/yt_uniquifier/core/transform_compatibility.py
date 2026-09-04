"""Declarative compatibility graph plus checks not tied to FFmpeg probing.

The graph is the single inventory used by runtime preflight tests and the
transform reference. Existing specialised preflight checks retain ownership of
media probing; this module owns cross-transform and profile-policy edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.transforms import get

CompatibilitySeverity = Literal["info", "warn", "fail"]
CompatibilityDomain = Literal["hdr", "audio", "temporal", "container", "quality"]


@dataclass(frozen=True)
class CompatibilityRule:
    code: str
    domain: CompatibilityDomain
    transforms: tuple[str, ...]
    severity: CompatibilitySeverity
    constraint: str


@dataclass(frozen=True)
class CompatibilityIssue:
    code: str
    severity: CompatibilitySeverity
    message: str
    suggestion: str


# Includes cross-transform rules enforced here and contextual rules enforced by
# specialised preflight checks. Keeping those edges visible in one graph avoids
# treating container/source/encoder constraints as undocumented exceptions.
COMPATIBILITY_GRAPH: tuple[CompatibilityRule, ...] = (
    CompatibilityRule(
        "hdr.mode.conflict", "hdr", ("video.tonemap_sdr",), "fail",
        "video.tonemap_sdr and keep_hdr=true are mutually exclusive output modes.",
    ),
    CompatibilityRule(
        "tonemap.not_first", "hdr", ("video.tonemap_sdr",), "fail",
        "HDR-to-SDR tonemap must be the first enabled video transform.",
    ),
    CompatibilityRule(
        "tonemap.sdr_input", "hdr", ("video.tonemap_sdr",), "fail",
        "HDR-to-SDR tonemap requires a PQ or HLG source.",
    ),
    CompatibilityRule(
        "tonemap.zscale.missing", "hdr", ("video.tonemap_sdr",), "fail",
        "HDR-to-SDR tonemap requires an FFmpeg build with zscale/libzimg.",
    ),
    CompatibilityRule(
        "hdr.output_policy.missing", "hdr", (), "fail",
        "HDR input requires explicit preservation or first-stage SDR tonemapping.",
    ),
    CompatibilityRule(
        "hdr.dynamic_metadata.unsupported", "hdr", (), "fail",
        "Dynamic Dolby Vision/HDR10+ metadata cannot be preserved by this pipeline.",
    ),
    CompatibilityRule(
        "hdr.zscale.missing", "hdr", (), "fail",
        "HDR preservation through colour transforms requires zscale/libzimg.",
    ),
    CompatibilityRule(
        "hdr.encoder.8bit", "hdr", (), "fail",
        "HDR preservation requires a probed 10-bit HEVC-capable encoder.",
    ),
    CompatibilityRule(
        "hdr.static_metadata.encoder_unverified", "hdr", (), "fail",
        "Static HDR metadata passthrough is verified only for libx265.",
    ),
    CompatibilityRule(
        "hdr.color.transforms", "hdr", ("video.color_eq", "video.noise"), "fail",
        "HDR colour-domain transforms require keep_hdr or prior SDR tonemapping.",
    ),
    CompatibilityRule(
        "hdr.blend.unwrapped", "hdr", ("video.blend_b",), "warn",
        "Two-source blend operates in the transfer domain and can shift HDR colour.",
    ),
    CompatibilityRule(
        "audio.haas_requires_stereo", "audio", ("audio.haas_stereo",), "fail",
        "Haas processing requires a two-channel main audio stream.",
    ),
    CompatibilityRule(
        "audio.loudnorm.order", "audio", ("audio.loudnorm",), "fail",
        "Loudness normalization must be the final enabled audio transform.",
    ),
    CompatibilityRule(
        "audio.loudnorm.duplicate", "audio", ("audio.loudnorm",), "fail",
        "A profile may contain at most one enabled loudness normalization pass.",
    ),
    CompatibilityRule(
        "timeline.rate_mismatch", "temporal",
        ("video.speed", "audio.pitch_tempo"), "fail",
        "Video rate and main-audio tempo must match.",
    ),
    CompatibilityRule(
        "timeline.passthrough_audio_rate", "temporal", ("video.speed",), "fail",
        "Retiming cannot keep additional stream-copy audio tracks synchronized.",
    ),
    CompatibilityRule(
        "timeline.aux_stream_rate", "temporal", ("video.speed",), "fail",
        "Retiming subtitles and chapters is not implemented safely.",
    ),
    CompatibilityRule(
        "quality.target_vmaf.unregistered_reference", "quality",
        ("video.speed", "video.temporal_jitter", "video.tonemap_sdr"), "fail",
        "Geometry/timeline transforms require a registered VMAF reference.",
    ),
    CompatibilityRule(
        "subs.image_based", "container", (), "fail",
        "Image subtitles require MKV; MP4/MOV cannot preserve them losslessly.",
    ),
    CompatibilityRule(
        "metadata.disposition.container_loss", "container", (), "warn",
        "MP4/MOV cannot represent every MKV stream disposition.",
    ),
    CompatibilityRule(
        "aux.attachments.unsupported", "container", (), "fail",
        "Attachments require a container that preserves attachment streams.",
    ),
    CompatibilityRule(
        "aux.data.unsupported", "container", (), "fail",
        "Data streams require a container/codec mapping that preserves them.",
    ),
    CompatibilityRule(
        "aux.attached_pic.unsupported", "container", (), "fail",
        "Cover art requires a compatible image codec/container combination.",
    ),
)


def evaluate_transform_compatibility(plan: Plan) -> list[CompatibilityIssue]:
    """Evaluate generic graph edges against an already validated plan."""
    enabled = [item for item in plan.profile.transforms if item.enabled]
    issues: list[CompatibilityIssue] = []
    ids = [item.id for item in enabled]

    if plan.profile.keep_hdr and "video.tonemap_sdr" in ids:
        issues.append(CompatibilityIssue(
            code="hdr.mode.conflict",
            severity="fail",
            message=(
                "keep_hdr=true conflicts with video.tonemap_sdr: one requests "
                "HDR preservation while the other produces SDR."
            ),
            suggestion="Choose exactly one output mode: keep HDR or tonemap to SDR.",
        ))

    loudnorm_positions = [
        index for index, item in enumerate(enabled) if item.id == "audio.loudnorm"
    ]
    if len(loudnorm_positions) > 1:
        issues.append(CompatibilityIssue(
            code="audio.loudnorm.duplicate",
            severity="fail",
            message="Multiple audio.loudnorm transforms would reuse one measurement.",
            suggestion="Keep exactly one audio.loudnorm transform.",
        ))
    elif loudnorm_positions:
        later_audio = [
            item.id for item in enabled[loudnorm_positions[0] + 1:]
            if get(item.id).kind == "audio"
        ]
        if later_audio:
            issues.append(CompatibilityIssue(
                code="audio.loudnorm.order",
                severity="fail",
                message=(
                    "audio.loudnorm is followed by audio transform(s) that can "
                    f"invalidate its measured output: {', '.join(later_audio)}."
                ),
                suggestion="Move audio.loudnorm after every other audio transform.",
            ))

    # Third-party transforms can declare symmetric or one-way pair edges in
    # TransformSpec. Evaluate each unordered pair once.
    seen_pairs: set[tuple[str, str]] = set()
    enabled_ids = set(ids)
    for transform_id in enabled_ids:
        spec = get(transform_id)
        for other in spec.incompatible_with:
            if other not in enabled_ids:
                continue
            ordered_pair = sorted((transform_id, other))
            pair = (ordered_pair[0], ordered_pair[1])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            issues.append(CompatibilityIssue(
                code="transform.incompatible_pair",
                severity="fail",
                message=f"Transforms {pair[0]} and {pair[1]} are incompatible.",
                suggestion="Disable one transform from the incompatible pair.",
            ))
    return issues


__all__ = [
    "COMPATIBILITY_GRAPH",
    "CompatibilityIssue",
    "CompatibilityRule",
    "evaluate_transform_compatibility",
]
