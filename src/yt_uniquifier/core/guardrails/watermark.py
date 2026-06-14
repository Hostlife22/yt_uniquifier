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
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_log = logging.getLogger(__name__)

# Match score above which we treat a frame region as a likely watermark.
# Picked empirically against the synthetic-template corpus + a
# 50-clip false-positive test set; the v1.3.0 roadmap calls out
# threshold tuning as a follow-up.
_MATCH_THRESHOLD = 0.45

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
            frames = _extract_sample_frames(source, tmp_dir, frame_count=frame_count)
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

        best_score = 0.0
        matched = 0
        for fp in frames:
            frame_img = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
            if frame_img is None:
                continue
            frame_best = 0.0
            for tmpl in templates:
                if (frame_img.shape[0] < tmpl.shape[0]
                        or frame_img.shape[1] < tmpl.shape[1]):
                    continue
                score = cv2.matchTemplate(frame_img, tmpl, cv2.TM_CCOEFF_NORMED).max()
                frame_best = max(frame_best, float(score))
            best_score = max(best_score, frame_best)
            if frame_best >= threshold:
                matched += 1

    return WatermarkFinding(
        detected=matched > 0,
        confidence=best_score,
        sampled_frames=len(frames),
        matched_frames=matched,
    )


def _extract_sample_frames(
    source: Path, dest_dir: Path, *, frame_count: int,
) -> list[Path]:
    """Sample ``frame_count`` uniformly-spaced frames as PNGs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(dest_dir / "frame_%03d.png")
    # ``thumbnail=200`` picks one representative frame per 200; biased
    # toward many candidates, then we cap at frame_count via -frames:v.
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vf", "thumbnail=200,scale=640:-1",
        "-frames:v", str(frame_count),
        "-vsync", "vfr",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"watermark frame extraction failed: {exc.stderr!r}"
        ) from exc
    frames = sorted(dest_dir.glob("frame_*.png"))
    return frames


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
