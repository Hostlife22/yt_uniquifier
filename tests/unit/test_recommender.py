"""v1.1.0 Task 21: deterministic profile recommender."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.models import (
    AudioStream,
    HDRInfo,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.recommender import explain, recommend


def _src(
    tmp_path: Path,
    *,
    width: int,
    height: int,
    duration_sec: float = 30.0,
    hdr: bool = False,
) -> SourceMeta:
    src = tmp_path / "x.mp4"
    src.touch()
    color = (
        HDRInfo(
            is_hdr=True, transfer="smpte2084", primaries="bt2020",
            space="bt2020nc", bit_depth=10,
        )
        if hdr
        else HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709", space="bt709")
    )
    return SourceMeta(
        path=src, container="mp4", duration_sec=duration_sec,
        size_bytes=10,
        video=[VideoStream(
            index=0, codec="h264", width=width, height=height,
            fps=24.0, duration_sec=duration_sec,
            pix_fmt="yuv420p10le" if hdr else "yuv420p",
            color=color,
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )


@pytest.mark.parametrize(
    ("kwargs", "expected_slug"),
    [
        ({"width": 1920, "height": 1080, "hdr": True}, "medium_hdr"),
        ({"width": 3840, "height": 2160}, "youtube_4k"),
        ({"width": 1080, "height": 1920, "duration_sec": 45}, "youtube_shorts"),
        ({"width": 1080, "height": 1920, "duration_sec": 200}, "tiktok_vertical"),
        ({"width": 1080, "height": 1080}, "instagram_square"),
        ({"width": 1920, "height": 1080}, "medium"),
        ({"width": 640, "height": 360}, "medium"),
    ],
)
def test_recommend_buckets(
    tmp_path: Path, kwargs: dict[str, object], expected_slug: str,
) -> None:
    src = _src(tmp_path, **kwargs)  # type: ignore[arg-type]
    assert recommend(src) == expected_slug


def test_explain_returns_reason(tmp_path: Path) -> None:
    src = _src(tmp_path, width=3840, height=2160)
    rec = explain(src)
    assert rec.slug == "youtube_4k"
    assert "3840" in rec.reason and "2160" in rec.reason


def test_recommender_falls_back_when_no_video(tmp_path: Path) -> None:
    src = _src(tmp_path, width=1920, height=1080)
    audio_only = src.model_copy(update={"video": []})
    assert recommend(audio_only) == "medium"


def test_recommended_slugs_match_shipped_profiles(tmp_path: Path) -> None:
    """Every slug the recommender can return MUST exist as a shipped
    YAML, otherwise ``--profile auto`` would fail at run time.
    """
    from yt_uniquifier.core import recommender as rec_mod

    profile_dir = (
        Path(rec_mod.__file__).resolve().parents[1] / "profiles"
    )
    for slug in {
        rec_mod._SLUG_HDR,
        rec_mod._SLUG_4K,
        rec_mod._SLUG_SHORTS,
        rec_mod._SLUG_TIKTOK,
        rec_mod._SLUG_SQUARE,
        rec_mod._SLUG_DEFAULT,
    }:
        assert (profile_dir / f"{slug}.yaml").exists(), (
            f"recommender returns {slug!r} but profiles/{slug}.yaml is missing"
        )
