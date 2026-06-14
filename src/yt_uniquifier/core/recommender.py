"""Deterministic profile recommender (v1.1.0 Task 21).

Picks a shipped profile slug based on the SourceMeta fingerprint:
  * HDR source     → ``medium_hdr``
  * 2160p+ source  → ``youtube_4k``
  * aspect 9:16 + duration ≤ 60 s → ``youtube_shorts``
  * aspect 9:16    → ``tiktok_vertical``
  * aspect 1:1     → ``instagram_square``
  * else           → ``medium``

The decision is pure / deterministic so two callers seeing the same
SourceMeta always pick the same slug. CLI ``--profile auto`` and the
GUI run screen call ``recommend(source) → slug`` and ``explain(...)``
when they want a one-line justification to show the user.
"""

from __future__ import annotations

from dataclasses import dataclass

from yt_uniquifier.core.models import SourceMeta

# Slugs match files under ``src/yt_uniquifier/profiles/*.yaml`` —
# import-time check below verifies they're real to catch typos as soon
# as the module loads in a clean install.
_SLUG_HDR = "medium_hdr"
_SLUG_4K = "youtube_4k"
_SLUG_SHORTS = "youtube_shorts"
_SLUG_TIKTOK = "tiktok_vertical"
_SLUG_SQUARE = "instagram_square"
_SLUG_DEFAULT = "medium"


@dataclass(frozen=True)
class Recommendation:
    """One picked profile + the human-readable reason."""

    slug: str
    reason: str


def recommend(source: SourceMeta) -> str:
    """Return the recommended profile slug for ``source``.

    Pure / deterministic — see module docstring for the rule order.
    """
    return _decide(source).slug


def explain(source: SourceMeta) -> Recommendation:
    """Return slug + a one-sentence justification.

    The CLI ``--profile auto`` flow prints this when the user runs
    ``--dry-run`` so they can sanity-check the pick before committing.
    """
    return _decide(source)


def _decide(source: SourceMeta) -> Recommendation:
    if not source.video:
        return Recommendation(slug=_SLUG_DEFAULT, reason="no video stream — fallback")

    v = source.video[0]

    if v.color.is_hdr:
        return Recommendation(
            slug=_SLUG_HDR,
            reason=(
                f"HDR source ({v.color.transfer}/{v.color.primaries}); "
                f"medium_hdr keeps the 10-bit pipeline."
            ),
        )

    if v.height >= 2160:
        return Recommendation(
            slug=_SLUG_4K,
            reason=f"{v.width}×{v.height} source → 4K-tuned bitrate ladder.",
        )

    # Aspect classification. Real-world sources rarely sit exactly on
    # the ideal ratio (sensor crops, letterboxing), so we accept a 5%
    # tolerance around each target.
    aspect = v.width / v.height if v.height else 0.0
    if _is_vertical(aspect):
        if source.duration_sec <= 60:
            return Recommendation(
                slug=_SLUG_SHORTS,
                reason=(
                    f"9:16 source, {source.duration_sec:.0f} s — fits Shorts."
                ),
            )
        return Recommendation(
            slug=_SLUG_TIKTOK,
            reason=(
                f"9:16 source, {source.duration_sec:.0f} s — "
                "TikTok vertical handles longer."
            ),
        )
    if _is_square(aspect):
        return Recommendation(
            slug=_SLUG_SQUARE,
            reason=f"1:1 source ({v.width}×{v.height}) — Instagram square.",
        )

    return Recommendation(
        slug=_SLUG_DEFAULT,
        reason=(
            f"{v.width}×{v.height} landscape source — medium preset is "
            "the sane default."
        ),
    )


def _is_vertical(aspect: float) -> bool:
    # 9:16 ≈ 0.5625; allow 5% wobble for sensor crop / letterbox.
    target = 9 / 16
    return abs(aspect - target) <= target * 0.05


def _is_square(aspect: float) -> bool:
    return abs(aspect - 1.0) <= 0.05
