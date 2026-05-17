"""dump_profile + load_profile roundtrip."""

from __future__ import annotations

from pathlib import Path

from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.core.profile_loader import dump_profile, load_profile


def test_minimal_profile_roundtrip(tmp_path: Path) -> None:
    p = Profile(name="t", transforms=[TransformConfig(id="audio.loudnorm")])
    out = tmp_path / "p.yaml"
    dump_profile(p, out)
    p2 = load_profile(out)
    assert p2 == p


def test_full_profile_roundtrip(tmp_path: Path) -> None:
    p = Profile(
        name="tuned",
        description="auto-generated",
        transforms=[
            TransformConfig(id="video.crop_resize", params={"max_strength": 0.08}),
            TransformConfig(id="video.color_eq", params={"brightness": 0.02}),
            TransformConfig(id="audio.pitch_tempo",
                            params={"pitch": 1.018, "randomize_within": 0.004}),
            TransformConfig(id="audio.eq",
                            params={"bands": [[110.0, -0.9], [3800.0, 0.6]]}),
            TransformConfig(id="audio.loudnorm"),
        ],
        seed_strategy="per_run",
        target_codec="h264",
    )
    out = tmp_path / "p.yaml"
    dump_profile(p, out)
    p2 = load_profile(out)
    assert p2 == p


def test_dump_excludes_none_keys(tmp_path: Path) -> None:
    p = Profile(name="t")
    out = tmp_path / "p.yaml"
    dump_profile(p, out)
    text = out.read_text(encoding="utf-8")
    # `description` defaults to None and shouldn't appear in the dump.
    assert "description:" not in text or "description: null" not in text
