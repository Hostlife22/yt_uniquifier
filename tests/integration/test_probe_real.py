"""End-to-end probe against a real generated clip."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.probe import probe


@needs_ffmpeg
@pytest.mark.integration
def test_probe_real_tiny_clip(tiny_clip: Path) -> None:
    meta = probe(tiny_clip)
    assert meta.container in {"mp4", "mov"}
    assert len(meta.video) == 1
    assert len(meta.audio) == 1

    v = meta.video[0]
    assert v.width == 320
    assert v.height == 180
    assert v.fps == pytest.approx(24.0, abs=0.1)
    assert 1.8 < v.duration_sec < 2.2
    assert v.color.is_hdr is False

    a = meta.audio[0]
    assert a.sample_rate == 44100 or a.sample_rate == 48000
    assert a.channels >= 1


@needs_ffmpeg
@pytest.mark.integration
def test_detect_and_pick_real(isolated_cache: Path) -> None:
    cands = detect_encoders(force=True)
    working = [c for c in cands if c.works]
    # libx264 must always be available with a sane ffmpeg build.
    assert any(c.name == "libx264" and c.works for c in cands), (
        f"libx264 not detected; full results: {cands}"
    )
    assert pick_encoder(working, codec="h264").works is True
