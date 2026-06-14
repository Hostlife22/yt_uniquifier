"""v1.3.0 Task 31 — DRM-encrypted-source detection.

Block encrypted MP4 / Matroska sources at preflight so the operator
can't accidentally drive yt-uniquifier into a DRM-stripping role.
Detection is read-only — we call ``ffprobe -show_format -show_streams``
and pattern-match the JSON for the canonical encryption markers:

  * MP4 / fMP4 — ``encryption`` flag on format or stream,
    ``cenc:default_KID`` / ``pssh`` boxes
  * Matroska — ``EncryptedClient`` or per-track ``ContentEncodingType=1``

Unlike the watermark guardrail (Task 30), this check has **no
override**.  Encrypted content is by definition not the operator's
to re-encode, and an ``--accept-drm-risk`` flag would only invite
abuse.  Operators who genuinely want to process unencrypted versions
of legally-owned content must obtain those copies through licensed
channels.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_log = logging.getLogger(__name__)

# Substrings (case-insensitive) treated as DRM markers when present in
# ffprobe's JSON output.  Conservative list — false positives here
# would block legitimate sources, so we focus on the well-known
# encryption containers/atoms.
_DRM_MARKERS: tuple[str, ...] = (
    "cenc:default_KID",
    "pssh",
    "EncryptedClient",
    "encryption_scheme",
    # Matroska ContentEncoding with type=1 means "encryption".
    '"contentencodingtype": "1"',
    # Tags often carried by Widevine / PlayReady / FairPlay.
    "widevine",
    "playready",
    "fairplay",
)


@dataclass(frozen=True)
class DrmFinding:
    """Result of a DRM probe.

    Three states:
      * ``is_encrypted=True``  → ffprobe parsed cleanly AND a DRM marker
        matched.  Preflight emits ``drm.encrypted`` at ``fail`` severity.
      * ``probe_failed=True``  → ffprobe couldn't read the file (corrupt
        container, missing moov, IO error, timeout).  We can't tell
        whether encryption is present, so preflight emits
        ``drm.probe_failed`` at ``warn`` — surfaces the gap without
        blocking legitimately corrupt-looking but non-encrypted inputs.
      * neither flag set → clean source, no finding.
    """

    is_encrypted: bool
    matched_marker: str | None = None
    note: str = ""
    probe_failed: bool = False


def detect_drm(source: Path) -> DrmFinding:
    """Run ``ffprobe`` and pattern-match the result for encryption markers.

    Raises ``PipelineError`` when ``ffprobe`` is unavailable — DRM
    detection is a load-bearing guardrail and silently skipping when
    the binary's missing would be worse than failing loudly.
    """
    if not source.exists():
        raise PipelineError(f"DRM guardrail: source not found {source}")
    ffprobe = _ffprobe_bin()
    cmd = [
        ffprobe, "-hide_banner", "-loglevel", "error",
        "-show_format", "-show_streams", "-print_format", "json",
        str(source),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise PipelineError(
            f"DRM guardrail: ffprobe not found ({exc}). Install ffmpeg.",
        ) from exc
    except subprocess.TimeoutExpired:
        return DrmFinding(
            is_encrypted=False, probe_failed=True,
            note="ffprobe timed out",
        )
    if proc.returncode != 0:
        return DrmFinding(
            is_encrypted=False, probe_failed=True,
            note=f"ffprobe exit {proc.returncode}: {proc.stderr.strip()[:200]}",
        )
    raw = proc.stdout
    lower = raw.lower()
    for marker in _DRM_MARKERS:
        if marker.lower() in lower:
            return DrmFinding(is_encrypted=True, matched_marker=marker)
    # Structural check: walk the JSON for an `encryption` field anywhere
    # in the tree.  Catches future ffprobe schema additions that don't
    # surface in our substring list.
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return DrmFinding(
            is_encrypted=False, probe_failed=True,
            note="ffprobe output is not valid JSON",
        )
    found = _walk_for_encryption(doc)
    if found is not None:
        return DrmFinding(is_encrypted=True, matched_marker=found)
    return DrmFinding(is_encrypted=False)


def _walk_for_encryption(node: object, path: str = "") -> str | None:
    """Recursively search JSON for any key/value pair hinting at DRM."""
    if isinstance(node, dict):
        for key, value in node.items():
            key_lower = str(key).lower()
            if "encryption" in key_lower or "drm" in key_lower:
                return f"{path}.{key}" if path else key
            sub = _walk_for_encryption(value, f"{path}.{key}" if path else key)
            if sub is not None:
                return sub
    elif isinstance(node, list):
        for i, item in enumerate(node):
            sub = _walk_for_encryption(item, f"{path}[{i}]")
            if sub is not None:
                return sub
    return None


def _ffprobe_bin() -> str:
    """Return the ffprobe binary path, mirroring ``ffmpeg_bin`` logic."""
    bin_path = ffmpeg_bin()
    # ffmpeg_bin() returns something like ``/usr/bin/ffmpeg`` or
    # ``ffmpeg``; swap the trailing ``ffmpeg`` for ``ffprobe``.
    if bin_path.endswith("ffmpeg"):
        return bin_path[: -len("ffmpeg")] + "ffprobe"
    return "ffprobe"
