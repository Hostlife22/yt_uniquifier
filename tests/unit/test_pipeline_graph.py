"""Unit tests for the FilterGraph builder.

We mock out:
- ffmpeg/ffprobe binary lookup so the constructed args use a fixed path,
- LoudnormMeasurement so we don't actually invoke ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core import pipeline as pipeline_mod
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    TransformConfig,
    VideoStream,
)
from yt_uniquifier.core.pipeline import FilterGraph, compute_plan_hash
from yt_uniquifier.core.transforms import audio_loudnorm
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement


@pytest.fixture(autouse=True)
def _stub_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_mod, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")


def _src(tmp_path: Path, *, hdr: bool = False, n_audio: int = 1) -> SourceMeta:
    color = (
        HDRInfo(
            is_hdr=True,
            transfer="smpte2084",
            primaries="bt2020",
            space="bt2020nc",
            bit_depth=10,
        )
        if hdr
        else HDRInfo(is_hdr=False, transfer="bt709", primaries="bt709", space="bt709")
    )
    pix = "yuv420p10le" if hdr else "yuv420p"
    audio = [
        AudioStream(
            index=1 + i,
            codec="aac",
            sample_rate=48000,
            channels=2,
            channel_layout="stereo",
            bit_rate=256000,
            language="eng" if i == 0 else None,
            is_default=(i == 0),
        )
        for i in range(n_audio)
    ]
    src_path = tmp_path / "src.mp4"
    src_path.touch()
    return SourceMeta(
        path=src_path,
        container="mp4",
        duration_sec=5.0,
        size_bytes=1000,
        video=[
            VideoStream(
                index=0, codec="h264", width=1920, height=1080, fps=24.0,
                duration_sec=5.0, pix_fmt=pix, bit_rate=8000000, color=color, is_default=True,
            )
        ],
        audio=audio,
    )


def _plan(source: SourceMeta, transforms: list[TransformConfig], **profile_kw: object) -> Plan:
    profile = Profile(name="t", transforms=transforms, **profile_kw)  # type: ignore[arg-type]
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(
        source=source,
        profile=profile,
        encoder=enc,
        plan_hash=compute_plan_hash(source, profile, enc),
    )


def test_video_only_chain(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(
            id="video.crop_resize",
            params={"max_strength": 0.02, "rng_seed": 42},
        ),
        TransformConfig(
            id="video.color_eq",
            params={
                "brightness": 0.01,
                "contrast": 1.02,
                "gamma": 0.99,
                "saturation": 1.03,
            },
        ),
        TransformConfig(id="video.noise", params={"strength": 3}),
    ])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()

    assert built.output_video_label.startswith("v")
    assert built.output_audio_label == "0:a:0"  # no audio transforms
    fc = built.filter_complex
    assert "crop=iw*" in fc
    assert "eq=brightness=0.01" in fc
    assert "noise=alls=3" in fc
    assert "format=yuv420p" in fc


def test_fit_aspect_reasserts_exact_canvas_at_tail(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(
            id="video.fit_aspect",
            params={"target_aspect": "16:9", "target_width": 3840, "target_height": 2160},
        ),
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.02}),
    ])

    built = FilterGraph(plan, tmp_path / "out.mp4").build()

    assert "scale=3840:2160,format=yuv420p" in built.filter_complex


def test_hdr_source_uses_10bit_when_keep_hdr(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [
        TransformConfig(id="video.crop_resize", params={"rng_seed": 7}),
    ], keep_hdr=True)
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "format=yuv420p10le" in built.filter_complex


def test_hdr_source_falls_back_to_8bit_when_keep_hdr_false(tmp_path: Path) -> None:
    src = _src(tmp_path, hdr=True)
    plan = _plan(src, [TransformConfig(id="video.crop_resize", params={"rng_seed": 7})])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "format=yuv420p" in built.filter_complex
    assert "yuv420p10le" not in built.filter_complex


def test_multitrack_audio_passthrough(tmp_path: Path) -> None:
    src = _src(tmp_path, n_audio=3)
    plan = _plan(src, [
        TransformConfig(id="video.crop_resize", params={"rng_seed": 7}),
    ])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    args = " ".join(built.args)
    # main audio mapped through filter graph, but no audio transforms -> a_label == "0:a:0"
    assert "[0:a:0]" not in built.filter_complex  # no audio chain emitted
    # `_src` allocates AudioStream index = 1+i, so n_audio=3 -> indices [1, 2, 3].
    # Main is index 1; passthrough tracks are mapped by their **source** indices
    # (2 and 3), not by their output slot. `-c:a:1/2 copy` still uses output slots.
    assert "-map 0:a:2?" in args
    assert "-map 0:a:3?" in args
    assert "-c:a:1 copy" in args
    assert "-c:a:2 copy" in args


def test_loudnorm_triggers_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def fake_measure(source: Path, params: object | None = None) -> LoudnormMeasurement:
        calls["n"] += 1
        return LoudnormMeasurement(
            input_i=-20.0, input_tp=-2.0, input_lra=5.0, input_thresh=-30.0,
            target_offset=0.0,
        )

    monkeypatch.setattr(pipeline_mod, "measure", fake_measure)
    monkeypatch.setattr(audio_loudnorm, "measure", fake_measure)

    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.crop_resize", params={"rng_seed": 7}),
        TransformConfig(id="audio.loudnorm"),
    ])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()

    assert calls["n"] == 1
    assert "loudnorm=I=-14.0" in built.filter_complex
    assert "measured_I=-20.0" in built.filter_complex
    assert built.loudnorm_measurement is not None


def test_loudnorm_uses_provided_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If we pass a measurement, no extra ffmpeg invocation is made."""
    called = {"n": 0}

    def fake_measure(*_a: object, **_k: object) -> LoudnormMeasurement:
        called["n"] += 1
        return LoudnormMeasurement(
            input_i=0, input_tp=0, input_lra=0, input_thresh=0, target_offset=0
        )

    monkeypatch.setattr(pipeline_mod, "measure", fake_measure)
    monkeypatch.setattr(audio_loudnorm, "measure", fake_measure)

    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="audio.loudnorm"),
    ])
    m = LoudnormMeasurement(
        input_i=-23.0, input_tp=-2.5, input_lra=7.0, input_thresh=-33.0, target_offset=0.1
    )
    built = FilterGraph(plan, tmp_path / "out.mp4", loudnorm_measurement=m).build()
    assert called["n"] == 0
    assert "measured_I=-23.0" in built.filter_complex
    assert built.loudnorm_measurement is m


def test_blend_b_extra_input_rewritten(tmp_path: Path) -> None:
    b = tmp_path / "b.mp4"
    b.touch()
    src = _src(tmp_path)
    plan = _plan(src, [
        TransformConfig(id="video.blend_b", params={"b_video_path": str(b), "opacity": 0.04}),
    ])
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    # placeholder __B__ must be replaced with the concrete [1:v] reference.
    assert "__B__" not in built.filter_complex
    assert "[1:v]" in built.filter_complex
    assert built.extra_inputs == [b]
    # ffmpeg args must contain a second -i for the B video.
    i_count = built.args.count("-i")
    assert i_count == 2


def test_encoder_args_per_vendor(tmp_path: Path) -> None:
    src = _src(tmp_path)
    profile = Profile(name="t", transforms=[
        TransformConfig(id="video.crop_resize", params={"rng_seed": 7}),
    ])
    for vendor, name, expected in [
        ("nvenc", "h264_nvenc", "-preset"),
        ("qsv", "h264_qsv", "-global_quality"),
        ("amf", "h264_amf", "-rc"),
        ("videotoolbox", "h264_videotoolbox", "-q:v"),
        ("x264", "libx264", "-crf"),
    ]:
        enc = EncoderCandidate(name=name, vendor=vendor, codec="h264", works=True)  # type: ignore[arg-type]
        plan = Plan(source=src, profile=profile, encoder=enc,
                    plan_hash=compute_plan_hash(src, profile, enc))
        built = FilterGraph(plan, tmp_path / f"out_{vendor}.mp4").build()
        assert expected in built.args, f"{vendor}: expected {expected} in args"
        assert name in built.args


def test_unknown_transform_raises(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, [TransformConfig(id="video.does_not_exist")])
    with pytest.raises(Exception, match="unknown transform"):
        FilterGraph(plan, tmp_path / "out.mp4").build()


def test_audio_chain_rejects_naked_in_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.0.1 Task 2: a chain builder that returns ``[in_label]...`` in
    its ``filter_str`` would otherwise produce a double-prefixed
    ``[in][in]...[out]`` filter_complex. ``_wrap_chain_str`` must detect
    this and raise PipelineError so the violation surfaces eagerly at
    ``build()`` rather than at ffmpeg invocation time.

    We register a misbehaving audio transform via a fresh entry in the
    transform registry, build a Plan, and assert ``PipelineError``.
    """
    from pydantic import BaseModel

    from yt_uniquifier.core.errors import PipelineError
    from yt_uniquifier.core.transforms import base as tbase

    class _NoOpParams(BaseModel):
        pass

    def _bad_audio_builder(
        params: _NoOpParams,
        alloc: tbase.LabelAllocator,
        in_label: str,
        *,
        rng: object | None = None,
    ) -> tbase.FilterChain:
        out_label = alloc.next("a")
        # Deliberately misbehaving: emit the [in_label] prefix ourselves.
        bad_filter_str = f"[{in_label}]volume=1.0"
        return tbase.FilterChain(
            in_label=in_label,
            out_label=out_label,
            filter_str=bad_filter_str,
        )

    spec_id = "audio.test_bad_prefix_v101"
    spec = tbase.TransformSpec(
        id=spec_id,
        kind="audio",
        schema=_NoOpParams,
        build=_bad_audio_builder,
        defaults={},
    )
    # The registry is module-global; register once per test process.
    if spec_id not in tbase._REGISTRY:
        tbase.register(spec)
    # Reset between identical-named tests in the same process: not needed
    # because we re-use the same spec each run.

    src = _src(tmp_path, n_audio=1)
    plan = _plan(src, [TransformConfig(id=spec_id)])
    with pytest.raises(PipelineError, match="emitted .* prefix"):
        FilterGraph(plan, tmp_path / "out.mp4").build()


# ---------------------------------------------------------------------------
# v1.2.0 Task 22 — AV1 encoder support
# ---------------------------------------------------------------------------


def _av1_plan(tmp_path: Path, vendor: str, name: str) -> Plan:
    src = _src(tmp_path)
    profile = Profile(name="av1t", target_codec="av1")
    enc = EncoderCandidate(name=name, vendor=vendor, codec="av1", works=True)  # type: ignore[arg-type]
    return Plan(
        source=src, profile=profile, encoder=enc,
        plan_hash=compute_plan_hash(src, profile, enc),
    )


def test_av1_default_crf_constant_distinct_from_x26x() -> None:
    """AV1 uses a 0..63 scale; libx264/libx265 use 0..51.  The two
    defaults must not silently collapse into one constant."""
    assert pipeline_mod._DEFAULT_AV1_CRF == 30
    assert pipeline_mod._DEFAULT_X26X_CRF == 18
    assert pipeline_mod._DEFAULT_AV1_CRF != pipeline_mod._DEFAULT_X26X_CRF


def test_av1_encoder_args_libsvtav1(tmp_path: Path) -> None:
    plan = _av1_plan(tmp_path, vendor="svtav1", name="libsvtav1")
    args = pipeline_mod._encoder_args_for(plan)
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "libsvtav1"
    assert args[args.index("-crf") + 1] == "30"
    assert "-preset" in args and args[args.index("-preset") + 1] == "8"
    # AV1 software encoders must emit -b:v 0 so CRF mode is unambiguous.
    bv_idx = args.index("-b:v")
    assert args[bv_idx + 1] == "0"


def test_av1_encoder_args_libaom(tmp_path: Path) -> None:
    plan = _av1_plan(tmp_path, vendor="libaom", name="libaom-av1")
    args = pipeline_mod._encoder_args_for(plan)
    assert args[args.index("-c:v") + 1] == "libaom-av1"
    assert args[args.index("-crf") + 1] == "30"
    # libaom-av1 specifics: cpu-used + row-mt + tiles.
    assert args[args.index("-cpu-used") + 1] == "4"
    assert args[args.index("-row-mt") + 1] == "1"


def test_av1_encoder_args_av1_nvenc_uses_gpu_quality(tmp_path: Path) -> None:
    """av1_nvenc maps onto the existing NVENC cq scale (0..51, default 19)
    so a single crf_override drives both software and hardware AV1."""
    plan = _av1_plan(tmp_path, vendor="nvenc", name="av1_nvenc")
    args = pipeline_mod._encoder_args_for(plan)
    cq_idx = args.index("-cq")
    assert args[cq_idx + 1] == "19"
    assert args[args.index("-c:v") + 1] == "av1_nvenc"


def test_av1_crf_override_clamps_to_63_not_51(tmp_path: Path) -> None:
    """For AV1, the clamp ceiling is 63 (the AV1 CRF max), not 51 as
    for libx264.  A target-VMAF loop that pushes CRF past 51 on AV1
    must not be silently truncated to libx264's range."""
    plan = _av1_plan(tmp_path, vendor="svtav1", name="libsvtav1")
    args = pipeline_mod._encoder_args_for(plan, crf_override=60)
    assert args[args.index("-crf") + 1] == "60"
    # Above the AV1 ceiling — clamps to 63, not 51.
    args = pipeline_mod._encoder_args_for(plan, crf_override=99)
    assert args[args.index("-crf") + 1] == "63"


def test_av1_filter_graph_builds(tmp_path: Path) -> None:
    """End-to-end smoke: an AV1 Plan produces a buildable FilterGraph
    with av1 encoder name in the args list."""
    src = _src(tmp_path)
    profile = Profile(name="av1t", target_codec="av1",
                      transforms=[TransformConfig(id="video.crop_resize",
                                                  params={"rng_seed": 7})])
    enc = EncoderCandidate(name="libsvtav1", vendor="svtav1", codec="av1", works=True)
    plan = Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))
    built = FilterGraph(plan, tmp_path / "out.mp4").build()
    assert "libsvtav1" in built.args
    assert "-crf" in built.args
