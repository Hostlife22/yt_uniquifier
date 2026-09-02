"""PySceneDetect adapter — content-aware segment boundaries.

v0.8.0 R3 — opt-in alternative to the default keyframe-aligned planner
in :func:`segmenter.plan_segments`. The function lives in its own
module so the lazy import of PySceneDetect (~10 MB transitively) only
happens when a profile actually requests ``segmentation.mode: scene``.

Architectural contract:
  * Caller passes a fully-resolved source path + ContentDetector params.
  * Returns plain floats (PTS in seconds), so the consumer never sees a
    PySceneDetect type — the dep is fully sandboxed inside this module.
  * Snap-to-keyframe is the consumer's responsibility (see
    :func:`snap_to_keyframes`); split-process-concat *requires* every
    boundary fall on a keyframe.

If PySceneDetect isn't installed (the package is in the ``[scene]``
extra), the call raises :class:`PipelineError` with the install hint
instead of letting an ``ImportError`` escape — bare ImportError reads
as "yt-uniquifier is broken" rather than "this profile needs an extra".
"""

from __future__ import annotations

import logging
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError

_log = logging.getLogger(__name__)

_INSTALL_HINT = (
    "PySceneDetect is required for segmentation.mode='scene'. "
    "Install with `pip install yt-uniquifier[scene]` (or "
    "`pip install 'scenedetect~=0.7.1'` if you manage extras yourself)."
)


def detect_scene_boundaries(
    source: Path,
    *,
    threshold: float = 27.0,
    min_length_sec: float = 2.0,
) -> list[float]:
    """Detect scene-change PTS (seconds) using PySceneDetect's ``ContentDetector``.

    Returns the timestamp of each scene's *start* (excluding the trivial
    first scene at 0.0). The consumer prepends 0 and appends duration
    when building the final segment list — keeps this function focused
    on "what does PySceneDetect see" rather than "what does the segmenter
    need".

    Determinism: PySceneDetect's ContentDetector is a pure per-frame
    pixel-difference computation, so two invocations on the same source
    bytes return identical timestamps. Resume across runs is therefore
    safe to recompute without a separate cache.
    """
    try:
        from scenedetect import ContentDetector, detect
    except ImportError as exc:
        raise PipelineError(_INSTALL_HINT) from exc

    if not source.exists():
        raise PipelineError(f"scene-detect source does not exist: {source}")

    # ``min_scene_len`` would normally enforce minimum-segment-length
    # inside ContentDetector, but its frames-vs-string handling drifts
    # between PySceneDetect minor versions (0.6.6 accepted "HH:MM:SS"
    # strings, 0.6.7 broke that path). We bypass the lib-side filter by
    # passing ``1`` (one frame) and re-apply the seconds-based filter
    # in :func:`snap_to_keyframes`, which we have to run anyway to honour
    # the stream-copy invariant. One source of truth, no FPS round-trip.
    detector = ContentDetector(threshold=threshold, min_scene_len=1)
    try:
        scene_list = detect(str(source), detector)
    except Exception as exc:  # noqa: BLE001 — third-party lib, wide failure surface
        raise PipelineError(
            f"PySceneDetect failed on {source}: {exc}"
        ) from exc

    boundaries: list[float] = []
    for start, _end in scene_list:
        try:
            # PySceneDetect 0.7 deprecates get_seconds(); the property exists on
            # both the supported 0.7 API and its FrameTimecode return type.
            t = float(start.seconds)
        except Exception:  # noqa: BLE001 — defensive against API drift
            continue
        if t > 0.0:
            boundaries.append(t)
    return sorted(boundaries)


def snap_to_keyframes(
    boundaries: list[float],
    keyframes: list[float],
    *,
    min_length_sec: float = 2.0,
) -> list[float]:
    """Snap each scene boundary down to the nearest keyframe ≤ boundary.

    Load-bearing: ``stream_copy_extract`` only produces clean output
    when ``-ss`` lands on a keyframe. A scene cut between two keyframes
    must round *down* (not nearest) so the preceding segment includes
    the whole tail of its last keyframe interval — otherwise we'd
    truncate frames the prior segment still owns.

    Multiple scene cuts inside the same keyframe interval collapse to a
    single boundary (deduplication). After snapping, boundaries closer
    than ``min_length_sec`` to each other are also collapsed — the user
    asked for at least N-second segments and ``ContentDetector.min_scene_len``
    enforces that at the pixel-diff level, but snapping can pull two
    distinct cuts onto the same keyframe and then *separate* them by a
    sub-second amount if the keyframe spacing is uneven.

    The result preserves order and is deduplicated; it does NOT include
    a leading 0.0 or trailing duration — those are the planner's job.
    """
    if not keyframes or not boundaries:
        return []

    sorted_kf = sorted(set(keyframes))
    out: list[float] = []
    for b in sorted(boundaries):
        # Largest keyframe ≤ b. Linear scan over a typical few-hundred
        # keyframe list is faster than the bisect+import dance and keeps
        # the code obvious.
        snapped: float | None = None
        for kf in sorted_kf:
            if kf <= b:
                snapped = kf
            else:
                break
        if snapped is None:
            continue
        if snapped <= 0.0:
            # 0.0 is the implicit first boundary; the planner adds it.
            continue
        if out and snapped - out[-1] < min_length_sec:
            continue
        if out and snapped == out[-1]:
            continue
        out.append(snapped)
    return out


__all__ = [
    "detect_scene_boundaries",
    "snap_to_keyframes",
]
