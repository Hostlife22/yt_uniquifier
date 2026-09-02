"""Unit tests for metadata args + title templates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from yt_uniquifier.core.metadata import build_metadata_args, resolve_title
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)


def _plan(tmp_path: Path, lang: str | None = "eng") -> Plan:
    src = tmp_path / "MyMovie.mkv"
    src.touch()
    source = SourceMeta(
        path=src, container="mp4", duration_sec=120.0, size_bytes=100,
        video=[VideoStream(index=0, codec="h264", width=1920, height=1080,
                           fps=24.0, duration_sec=120.0, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(
            index=1,
            codec="aac",
            sample_rate=48000,
            channels=2,
            language=lang,
            title="Main mix",
            is_default=True,
            dispositions=("default", "original"),
        )],
    )
    profile = Profile(name="medium", transforms=[])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=source, profile=profile, encoder=enc, plan_hash="abcdef0123456789")


def test_resolve_title_substitutions(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    out = resolve_title(
        "{stem}_{profile}_{hash8}", plan.source, plan.profile, plan.plan_hash
    )
    assert out == "MyMovie_medium_abcdef01"


def test_resolve_title_date_token(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    out = resolve_title("{stem} ({date})", plan.source, plan.profile, plan.plan_hash)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert today in out


def test_metadata_args_minimum(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    args = build_metadata_args(plan, creation_time=datetime(2026, 5, 17, 21, 0, 0, tzinfo=UTC))
    # Must strip original metadata first.
    assert args[:2] == ["-map_metadata", "-1"]
    # No tool-specific encoder=… signature (fingerprint hygiene).
    assert not any("yt-uniquifier" in s for s in args)
    assert any("creation_time=2026-05-17T21:00:00" in s for s in args)
    # Language tags propagated for each audio stream.
    assert "-metadata:s:a:0" in args
    assert "language=eng" in args
    assert "title=Main mix" in args
    assert "handler_name=Main mix" in args
    assert "-disposition:a:0" in args
    assert "default+original" in args


def test_metadata_no_language_tag_skipped(tmp_path: Path) -> None:
    plan = _plan(tmp_path, lang=None)
    args = build_metadata_args(plan)
    assert not any("language=" in s for s in args)


def test_metadata_title_template(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    args = build_metadata_args(plan, title_template="{stem} - {profile}")
    assert "title=MyMovie - medium" in args


def test_matroska_does_not_gain_mov_handler_name(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan = plan.model_copy(update={
        "profile": plan.profile.model_copy(update={"output_container": "mkv"}),
    })

    args = build_metadata_args(plan)

    assert "title=Main mix" in args
    assert "handler_name=Main mix" not in args
