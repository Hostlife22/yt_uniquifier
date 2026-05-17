"""Unit tests for the pHash compare logic — mocked frame extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from yt_uniquifier.core.qa import phash as phash_mod


def _solid_image(color: tuple[int, int, int], size: int = 256) -> Image.Image:
    return Image.new("RGB", (size, size), color)


def _gradient_image(seed: int, size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size))
    for y in range(size):
        for x in range(size):
            img.putpixel((x, y), ((x + seed) % 256, (y + seed) % 256, 0))
    return img


def test_identical_frames_high_similarity(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    img = _gradient_image(0)
    frames = [img.copy() for _ in range(5)]
    monkeypatch.setattr(phash_mod, "sample_frames",
                        lambda _p, n=None: list(frames))
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = phash_mod.compare(a, b)
    assert res.samples == 5
    assert res.similarity > 0.99
    assert res.distance_max == 0


def test_completely_different_frames_low_similarity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a_frames = [_gradient_image(0) for _ in range(4)]
    b_frames = [_solid_image((255, 0, 0)) for _ in range(4)]

    calls: list[Any] = []

    def fake(p: Path, n: int = 0) -> list[Image.Image]:
        calls.append(p)
        return a_frames if len(calls) % 2 == 1 else b_frames

    monkeypatch.setattr(phash_mod, "sample_frames", fake)
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = phash_mod.compare(a, b)
    assert res.samples == 4
    assert res.similarity < 0.9
    assert res.distance_max > 0


def test_no_frames_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(phash_mod, "sample_frames", lambda _p, n=None: [])
    a = tmp_path / "a.mp4"

    a.touch()
    b = tmp_path / "b.mp4"

    b.touch()
    res = phash_mod.compare(a, b)
    assert res.samples == 0
    assert res.similarity == 0.0


def test_split_png_stream_handles_concatenated_png(tmp_path: Path) -> None:
    import io
    img1 = _solid_image((1, 1, 1))
    img2 = _solid_image((2, 2, 2))
    buf1 = io.BytesIO()
    img1.save(buf1, format="PNG")
    buf2 = io.BytesIO()
    img2.save(buf2, format="PNG")
    blob = buf1.getvalue() + buf2.getvalue()
    parts = phash_mod._split_png_stream(blob)
    assert len(parts) == 2
