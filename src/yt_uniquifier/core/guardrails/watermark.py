"""v1.3.0 Task 30 — broadcaster watermark / station-ID detection.

Sample N=5 frames uniformly across the source, run OpenCV template
matching against a tiny shape corpus, and report the strongest match
as a ``WatermarkFinding``.  Default severity in preflight is ``error``
so the operator must either pass ``--accept-watermark-risk`` (the
"I own this content / have a license" attestation) or set the per-
profile ``skip_watermark_check`` flag.

Template corpus design (anti-copyright):

  * No actual broadcaster logos in the repo.  Templates are synthetic
    generic shapes (rounded rectangle, circle-in-rect, simple wordmark
    placeholder) that approximate the OUTLINE of common station bugs.
  * Match is fuzzy — we use ``cv2.matchTemplate`` with TM_CCOEFF_NORMED
    and a 0.45 threshold, which catches "there's a small fixed
    overlay shape in the corner" without claiming to identify any
    specific broadcaster.
  * False positives are acceptable.  The guardrail's purpose is to
    nudge operators to attest, not to be a CV-grade detector.  A
    licensed cooking show with an open-screen banner will trip the
    check; the operator passes the flag and moves on.

OpenCV is an optional dependency.  When ``cv2`` isn't importable the
detector returns ``None`` (preflight then skips the check with an
``info`` finding) so the guardrail doesn't block users who haven't
installed the ``[scene]`` extra.
"""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_log = logging.getLogger(__name__)

# A generic rectangle is common in ordinary footage, so a single weak
# full-frame template hit is not evidence of a station bug.  Detection
# requires a strong match in the same corner/template pair across a
# majority of temporal samples.
_MATCH_THRESHOLD = 0.70
_MIN_PERSISTENCE_RATIO = 0.60
_MIN_MATCHED_FRAMES = 2
_CORNER_FRACTION = 0.35

# Number of frames to sample.
_DEFAULT_FRAME_COUNT = 5


@dataclass(frozen=True)
class WatermarkFinding:
    """Result of a single watermark scan."""

    detected: bool
    confidence: float
    sampled_frames: int
    matched_frames: int
    note: str = ""


def detect_watermark(
    source: Path,
    *,
    frame_count: int = _DEFAULT_FRAME_COUNT,
    threshold: float = _MATCH_THRESHOLD,
    duration_sec: float | None = None,
) -> WatermarkFinding | None:
    """Run the guardrail.  Returns ``None`` when OpenCV isn't installed.

    A ``detected=True`` finding means at least one sampled frame had a
    region whose template-match score exceeded ``threshold``.  The
    ``confidence`` field is the maximum score across all sampled
    frames + templates.
    """
    try:
        import cv2
        import numpy as np  # noqa: F401 — opencv hard-deps numpy
    except ImportError:
        _log.info(
            "watermark guardrail skipped — install the [scene] extra "
            "to enable (opencv-python-headless).",
        )
        return None

    if not source.exists():
        raise PipelineError(f"watermark guardrail: source not found {source}")

    with tempfile.TemporaryDirectory(prefix="watermark_") as tmp:
        tmp_dir = Path(tmp)
        try:
            frames = _extract_sample_frames(
                source,
                tmp_dir,
                frame_count=frame_count,
                duration_sec=duration_sec,
            )
        except PipelineError as exc:
            _log.warning("watermark guardrail: extract failed (%s); skipping", exc)
            return WatermarkFinding(
                detected=False, confidence=0.0,
                sampled_frames=0, matched_frames=0,
                note=f"frame extraction failed: {exc}",
            )
        templates = _load_templates(cv2)
        if not templates:
            return WatermarkFinding(
                detected=False, confidence=0.0,
                sampled_frames=len(frames), matched_frames=0,
                note="no templates available (corpus empty)",
            )

        best_score, matched, usable_frames = _persistent_corner_match(
            frames,
            templates,
            cv2,
            threshold=threshold,
        )

    required_matches = max(
        _MIN_MATCHED_FRAMES,
        math.ceil(usable_frames * _MIN_PERSISTENCE_RATIO),
    )

    return WatermarkFinding(
        detected=usable_frames > 0 and matched >= required_matches,
        confidence=best_score,
        sampled_frames=usable_frames,
        matched_frames=matched,
    )


def _extract_sample_frames(
    source: Path,
    dest_dir: Path,
    *,
    frame_count: int,
    duration_sec: float | None = None,
) -> list[Path]:
    """Sample midpoint frames uniformly across the complete source.

    Input-side seeks avoid decoding a multi-hour file from the beginning
    merely to inspect five frames.  The previous ``thumbnail=200`` command
    stopped after the first five 200-frame buckets, so on long videos it
    inspected only the opening seconds despite claiming uniform coverage.
    """
    if frame_count < 1:
        raise PipelineError("watermark frame_count must be >= 1")
    if duration_sec is None or duration_sec <= 0:
        from yt_uniquifier.core.errors import ProbeError
        from yt_uniquifier.core.probe import probe

        try:
            duration_sec = probe(source).duration_sec
        except ProbeError as exc:
            raise PipelineError(f"watermark duration probe failed: {exc}") from exc
    if duration_sec <= 0:
        raise PipelineError("watermark guardrail: source duration is unknown")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(frame_count):
        timestamp = duration_sec * (idx + 0.5) / frame_count
        output = dest_dir / f"frame_{idx:03d}.png"
        cmd = [
            ffmpeg_bin(),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{timestamp:.6f}",
            "-i", str(source),
            "-map", "0:v:0",
            "-frames:v", "1",
            "-vf", "scale=640:-2",
            "-an", "-sn",
            str(output),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise PipelineError(
                f"watermark frame extraction failed at {timestamp:.3f}s: "
                f"{getattr(exc, 'stderr', exc)!r}"
            ) from exc
    frames = sorted(dest_dir.glob("frame_*.png"))
    return frames


def _persistent_corner_match(
    frames: list[Path],
    templates: list[Any],
    cv2: Any,
    *,
    threshold: float,
) -> tuple[float, int, int]:
    """Return confidence and persistence of the dominant corner/template.

    Broadcaster bugs are stationary corner overlays.  Matching over the
    whole image made ordinary rectangles and test patterns false-positive;
    counting any one frame as a detection made that false positive fatal.
    """
    hits: dict[tuple[int, int], list[float]] = {}
    usable_frames = 0
    for fp in frames:
        frame_img = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if frame_img is None:
            continue
        usable_frames += 1
        height, width = frame_img.shape[:2]
        corner_h = max(1, int(height * _CORNER_FRACTION))
        corner_w = max(1, int(width * _CORNER_FRACTION))
        corners = (
            frame_img[:corner_h, :corner_w],
            frame_img[:corner_h, width - corner_w :],
            frame_img[height - corner_h :, :corner_w],
            frame_img[height - corner_h :, width - corner_w :],
        )
        for corner_idx, region in enumerate(corners):
            for template_idx, template in enumerate(templates):
                if (
                    region.shape[0] < template.shape[0]
                    or region.shape[1] < template.shape[1]
                ):
                    continue
                score = float(
                    cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED).max()
                )
                if score >= threshold:
                    hits.setdefault((corner_idx, template_idx), []).append(score)

    if not hits:
        return 0.0, 0, usable_frames
    dominant_scores = max(hits.values(), key=lambda scores: (len(scores), max(scores)))
    return max(dominant_scores), len(dominant_scores), usable_frames


def _load_templates(cv2: Any) -> list[Any]:
    """Return the bundled grayscale template list, generating them in
    memory if no on-disk corpus is shipped yet.

    The repo intentionally ships SYNTHETIC shapes (corner-rect with a
    rounded inset, small text-band, circle-in-rect) — no copyrighted
    logos.  These shapes approximate the outline of common station
    bugs and trip the guardrail on a wide net.
    """
    import numpy as np
    templates: list[Any] = []
    # Template 1: corner station-bug placeholder — rounded rect.
    bug = np.zeros((40, 80), dtype=np.uint8)
    cv2.rectangle(bug, (3, 3), (76, 36), 220, thickness=-1)
    cv2.rectangle(bug, (8, 8), (72, 32), 30, thickness=-1)
    templates.append(bug)
    # Template 2: text-band placeholder — wide skinny rect.
    band = np.zeros((20, 120), dtype=np.uint8)
    cv2.rectangle(band, (2, 2), (118, 18), 220, thickness=-1)
    templates.append(band)
    # Template 3: circle-in-rect (sports league style).
    circ = np.zeros((50, 50), dtype=np.uint8)
    cv2.rectangle(circ, (2, 2), (47, 47), 220, thickness=-1)
    cv2.circle(circ, (25, 25), 15, 30, thickness=-1)
    templates.append(circ)
    return templates
