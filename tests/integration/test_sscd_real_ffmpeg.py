"""v0.8.0 R4 — SSCD integration test with real torch + real ffmpeg.

Skipped unless both:
  * ffmpeg is on PATH (``needs_ffmpeg`` from conftest), and
  * the ``[ml]`` extra is installed (``torch`` import succeeds).

Property: SSCD similarity of a clip vs itself must be ≈ 1.0. This is
the strongest contract we can assert without re-running Meta's full
DISC21 eval. If the embeddings drift below the band threshold for
self-similarity the model file is wrong or the preprocessing is broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg

torch = pytest.importorskip("torch")  # noqa: F841
pytest.importorskip("torchvision")
pytest.importorskip("PIL")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.ml,
]


@needs_ffmpeg
def test_sscd_self_similarity_is_near_one(tiny_clip: Path, tmp_path: Path) -> None:
    """A clip compared against itself must score ≈ 1.0.

    Uses the real downloaded model (cached after first call). On a fresh
    cache this test pays ~80 MB of network + ~5-10 s of CPU embedding.
    """
    from yt_uniquifier.core.qa.sscd import compute_sscd

    # Reuse the fixture clip as both source and output.
    res = compute_sscd(tiny_clip, tiny_clip, frame_count=8)
    # tiny_clip is 2 s @ 24 fps = 48 frames; thumbnail filter may still
    # emit fewer than the requested 8, so accept anything ≥ 4.
    assert len(res.per_frame) >= 4
    # The model is deterministic: every cosine should be effectively 1.0.
    # Even with float rounding through the GeM head + L2 normalisation,
    # 0.999 is a safe lower bound.
    assert res.min_similarity > 0.999, (
        f"self-similarity dropped to {res.min_similarity}; "
        f"per-frame: {res.per_frame}"
    )
    assert res.mean_similarity > 0.999


@needs_ffmpeg
def test_sscd_two_unrelated_clips_score_lower(
    tiny_clip: Path, tmp_path: Path
) -> None:
    """A different-content clip vs the testsrc2 fixture must score noticeably
    below the self-similarity threshold. Loose lower bound — we're not
    asserting "low" precisely, just "not 1.0"."""
    import subprocess

    from yt_uniquifier.core.qa.sscd import compute_sscd

    other = tmp_path / "other.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=blue:size=320x180:rate=24:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(other),
        ],
        check=True, capture_output=True, timeout=30,
    )

    res = compute_sscd(tiny_clip, other, frame_count=8)
    # testsrc2 (color bars + counter) vs flat blue should not look the
    # same to SSCD. Threshold is loose — the goal is "the metric
    # actually discriminates," not a precise drop number.
    assert res.mean_similarity < 0.95, (
        f"unrelated clips scored {res.mean_similarity}; "
        f"per-frame: {res.per_frame}"
    )
