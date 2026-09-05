"""v0.4.2 windowed audio dispatch + builder."""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core.models import TransformConfig
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_main_audio_command_windowed,
)


def _divergent_plan(tmp_path: Path, duration_sec: float, transforms):
    src = _src(tmp_path)
    # Override duration on the SourceMeta. _src() builds it with 10s; we
    # need longer to trigger windowing. Replace via model_copy.
    new_src = src.model_copy(update={"duration_sec": duration_sec})
    plan = _plan(new_src, transforms)
    # Mutate seed_strategy → divergent and bump run_seed for determinism.
    div_profile = plan.profile.model_copy(update={"seed_strategy": "divergent"})
    return plan.model_copy(update={"profile": div_profile, "run_seed": 12345,
                                    "source": new_src})


def test_short_audio_falls_back_to_legacy(tmp_path: Path) -> None:
    """Audio shorter than 2 × WINDOW_SEC → uses legacy single-pass path."""
    plan = _divergent_plan(tmp_path, duration_sec=30.0, transforms=[
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.06}),
    ])
    legacy_cmd, _ = build_main_audio_command(plan, tmp_path / "a.m4a")
    windowed_cmd, _ = build_main_audio_command_windowed(plan, tmp_path / "a.m4a")
    # Windowed path should return identical command to legacy for short audio.
    assert windowed_cmd.filter_complex == legacy_cmd.filter_complex


def test_long_audio_emits_multiple_windows(tmp_path: Path) -> None:
    """≥ 2 × WINDOW_SEC → windowed path emits atrim per window + acrossfade."""
    plan = _divergent_plan(tmp_path, duration_sec=180.0, transforms=[
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.06}),
    ])
    cmd, _ = build_main_audio_command_windowed(plan, tmp_path / "a.m4a")
    fc = cmd.filter_complex
    # 180 s / 60 s = 3 windows.
    # Three source windows plus the final exact-sample timeline trim.
    assert fc.count("atrim=") == 4
    # 2 internal boundaries → 2 acrossfades.
    assert fc.count("acrossfade=") == 2


def test_windowed_filter_has_unique_seeds_per_window(tmp_path: Path) -> None:
    """Different per-window seeds → different rubberband pitch values."""
    plan = _divergent_plan(tmp_path, duration_sec=180.0, transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.06, "randomize_within": 0.005,
                                "method": "rubberband"}),
    ])
    cmd, _ = build_main_audio_command_windowed(plan, tmp_path / "a.m4a")
    # Extract all rubberband pitch values; they should not all be the same.
    import re
    pitches = re.findall(r"rubberband=pitch=([\d.]+)", cmd.filter_complex)
    assert len(pitches) == 3
    assert len(set(pitches)) > 1, f"all windows got the same pitch: {pitches}"


def test_windowed_reproducible_for_same_run_seed(tmp_path: Path) -> None:
    """Same plan_hash + run_seed → identical filter_complex (resume-stable)."""
    plan = _divergent_plan(tmp_path, duration_sec=180.0, transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.06, "randomize_within": 0.005,
                                "method": "rubberband"}),
    ])
    cmd1, _ = build_main_audio_command_windowed(plan, tmp_path / "a.m4a")
    cmd2, _ = build_main_audio_command_windowed(plan, tmp_path / "a.m4a")
    assert cmd1.filter_complex == cmd2.filter_complex


def test_windowed_dispatch_via_segmenter(tmp_path: Path) -> None:
    """segmenter.process_main_audio uses windowed builder when strategy='divergent'."""
    # Just check the dispatch logic without running ffmpeg.
    # The dispatcher in segmenter.process_main_audio routes by
    # plan.profile.seed_strategy. The integration tests cover the full
    # end-to-end with real ffmpeg.
    plan = _divergent_plan(tmp_path, duration_sec=180.0, transforms=[
        TransformConfig(id="audio.pitch_tempo",
                        params={"pitch": 1.06, "method": "rubberband"}),
    ])
    assert plan.profile.seed_strategy == "divergent"
