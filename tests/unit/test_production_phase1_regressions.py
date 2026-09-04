"""Regression tests for the production-audit Phase 1 correctness fixes."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core.calibration.loop import _evaluate_sscd
from yt_uniquifier.core.models import AudioStream, TransformConfig
from yt_uniquifier.core.pipeline import (
    build_main_audio_command,
    build_main_audio_command_windowed,
    compute_plan_hash,
    expected_output_duration,
)


def test_asetrate_uses_actual_input_sample_rate_and_outputs_48k(tmp_path: Path) -> None:
    source = _src(tmp_path).model_copy(update={
        "audio": [AudioStream(index=1, codec="aac", sample_rate=44_100, channels=2)],
    })
    plan = _plan(source, [
        TransformConfig(
            id="audio.pitch_tempo",
            params={"pitch": 1.0004, "tempo": 1.0, "sample_rate": 48_000},
        ),
    ])

    command, _ = build_main_audio_command(plan, tmp_path / "audio.m4a")

    assert "asetrate=44100*1.000400" in command.filter_complex
    assert command.args[command.args.index("-ar") + 1] == "48000"


def test_profile_loudness_target_is_used_when_transform_has_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core import pipeline as pipeline_mod
    from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement

    monkeypatch.setattr(
        pipeline_mod,
        "measure",
        lambda *_args, **_kwargs: LoudnormMeasurement(
            input_i=-20.0,
            input_tp=-3.0,
            input_lra=4.0,
            input_thresh=-30.0,
            target_offset=0.0,
        ),
    )
    plan = _plan(
        _src(tmp_path),
        [TransformConfig(id="audio.loudnorm")],
        target_loudness_lufs=-16.0,
    )

    command, _ = build_main_audio_command(plan, tmp_path / "audio.m4a")

    assert "loudnorm=I=-16.0" in command.filter_complex
    assert "asetpts=N/SR/TB" in command.filter_complex
    assert command.filter_complex.endswith(f"[{command.output_audio_label}]")


def test_loudnorm_pass_one_measures_preceding_audio_transforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core import pipeline as pipeline_mod
    from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement

    captured: dict[str, object] = {}

    def fake_measure(*_args: object, **kwargs: object) -> LoudnormMeasurement:
        captured.update(kwargs)
        return LoudnormMeasurement(
            input_i=-20.0, input_tp=-3.0, input_lra=4.0,
            input_thresh=-30.0, target_offset=0.0,
        )

    monkeypatch.setattr(pipeline_mod, "measure", fake_measure)
    plan = _plan(_src(tmp_path), [
        TransformConfig(id="audio.eq"),
        TransformConfig(id="audio.pitch_tempo", params={"pitch": 1.01}),
        TransformConfig(id="audio.loudnorm"),
    ])

    build_main_audio_command(plan, tmp_path / "audio.m4a")

    pass_one = str(captured["pre_filter_complex"])
    assert "equalizer=" in pass_one
    assert "asetrate=" in pass_one
    assert captured["pre_output_label"]


def test_loudnorm_jitter_uses_same_target_in_both_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    from yt_uniquifier.core import pipeline as pipeline_mod
    from yt_uniquifier.core.transforms.audio_loudnorm import (
        LoudnormMeasurement,
        LoudnormParams,
    )

    captured: dict[str, float] = {}

    def fake_measure(_source: Path, params: LoudnormParams, **_kwargs: object):
        captured["integrated"] = params.integrated
        return LoudnormMeasurement(
            input_i=-20.0, input_tp=-3.0, input_lra=4.0,
            input_thresh=-30.0, target_offset=0.0,
        )

    monkeypatch.setattr(pipeline_mod, "measure", fake_measure)
    plan = _plan(_src(tmp_path), [
        TransformConfig(id="audio.loudnorm", params={"target_jitter_lufs": 1.0}),
    ]).model_copy(update={"run_seed": 123})

    command, _ = build_main_audio_command(plan, tmp_path / "audio.m4a")

    match = re.search(r"loudnorm=I=([-\d.]+)", command.filter_complex)
    assert match is not None
    assert float(match.group(1)) == pytest.approx(captured["integrated"])


def test_window_overlap_equals_crossfade_duration(tmp_path: Path) -> None:
    source = _src(tmp_path).model_copy(update={"duration_sec": 120.0})
    plan = _plan(source, [TransformConfig(id="audio.eq")])
    profile = plan.profile.model_copy(update={"seed_strategy": "divergent"})
    plan = plan.model_copy(update={"profile": profile, "source": source})

    command, _ = build_main_audio_command_windowed(plan, tmp_path / "audio.m4a")

    # The two 60-second windows must overlap by exactly the 0.1-second
    # crossfade, not by 0.2 seconds (which grows output by 0.1 s/boundary).
    assert "atrim=start=0.0000:end=60.0500" in command.filter_complex
    assert "atrim=start=59.9500:end=120.0000" in command.filter_complex
    assert "acrossfade=d=0.1" in command.filter_complex
    assert "asetpts=N/SR/TB" in command.filter_complex
    assert command.filter_complex.endswith(f"[{command.output_audio_label}]")


def test_sscd_evaluator_returns_direct_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yt_uniquifier.core.qa.sscd as sscd_mod

    class _Result:
        mean_similarity = 0.92
        min_similarity = 0.80
        per_frame = (0.92,)

    monkeypatch.setattr(sscd_mod, "compute_sscd", lambda *_a, **_kw: _Result())

    assert _evaluate_sscd(Path("source"), Path("output"), None) == pytest.approx(0.92)


def test_plan_hash_changes_with_stream_topology(tmp_path: Path) -> None:
    source = _src(tmp_path)
    plan = _plan(source, [])
    changed = source.model_copy(update={"audio": []})

    assert compute_plan_hash(source, plan.profile, plan.encoder) != compute_plan_hash(
        changed, plan.profile, plan.encoder,
    )


def test_plan_hash_changes_when_same_size_file_content_changes(tmp_path: Path) -> None:
    source = _src(tmp_path)
    source.path.write_bytes(b"aaaa")
    source = source.model_copy(update={"size_bytes": 4})
    plan = _plan(source, [])
    before = compute_plan_hash(source, plan.profile, plan.encoder)

    source.path.write_bytes(b"bbbb")
    after = compute_plan_hash(source, plan.profile, plan.encoder)

    assert before != after


def test_expected_duration_accounts_for_video_speed(tmp_path: Path) -> None:
    plan = _plan(
        _src(tmp_path),
        [TransformConfig(id="video.speed", params={"rate": 0.5})],
    )
    assert expected_output_duration(plan) == pytest.approx(10.0)
