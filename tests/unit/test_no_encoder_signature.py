"""v0.4.0: output must NOT carry a `yt-uniquifier` literal in metadata.

Stripped in pipeline.py FilterGraph._metadata_args. The ffmpeg muxer
writes its own `encoder=Lavf<version>` which is indistinguishable from
any other ffmpeg-built upload; that's fine. A custom string would
fingerprint the file as tool-generated.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core.metadata import build_metadata_args
from yt_uniquifier.core.models import TransformConfig
from yt_uniquifier.core.pipeline import FilterGraph, build_video_segment_command


def test_full_mode_no_yt_uniquifier_signature(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    joined = " ".join(built.args)
    assert "yt-uniquifier" not in joined, (
        f"output args carry tool signature: {joined}"
    )


def test_full_mode_clears_source_metadata(tmp_path: Path) -> None:
    """-map_metadata -1 is still present (strips source metadata)."""
    src = _src(tmp_path)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "-map_metadata" in built.args
    idx = built.args.index("-map_metadata")
    assert built.args[idx + 1] == "-1"


def test_segment_mode_no_yt_uniquifier_signature(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    seg_in = tmp_path / "seg_src.mkv"
    seg_in.touch()
    built = build_video_segment_command(plan, seg_in, tmp_path / "seg_out.mkv")
    joined = " ".join(built.args)
    assert "yt-uniquifier" not in joined


def test_concat_metadata_args_no_yt_uniquifier_signature(tmp_path: Path) -> None:
    """build_metadata_args runs on the final concat output (orchestrator
    path). It must mirror the pipeline policy and not leak a tool tag."""
    src = _src(tmp_path)
    plan = _plan(src, [TransformConfig(id="video.crop_resize")])
    args = build_metadata_args(plan)
    joined = " ".join(args)
    assert "yt-uniquifier" not in joined
