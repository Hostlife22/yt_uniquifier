from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core import media_validation
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import AudioStream, Chapter, SourceMeta, TransformConfig


def test_media_contract_detects_missing_audio_and_chapters(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path)
    source = source.model_copy(update={
        "chapters": [Chapter(start_sec=0.0, end_sec=5.0, title="Chapter")],
    })
    plan = _plan(source, [])
    output_meta = SourceMeta(
        path=tmp_path / "out.mp4", container="mp4", duration_sec=5.0,
        size_bytes=100, video=source.video, audio=[], chapters=[],
    )
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert not report.valid
    assert {failure.code for failure in report.failures} == {
        "streams.audio", "chapters.count",
    }


def test_media_contract_accepts_matching_topology(tmp_path: Path, monkeypatch) -> None:
    source = _src(tmp_path)
    plan = _plan(source, [])
    output_meta = source.model_copy(update={"path": tmp_path / "out.mp4"})
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    assert media_validation.inspect_output_contract(plan, output_meta.path).valid


def test_require_output_decode_raises_on_corrupt_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "broken.mp4"
    output.touch()

    def fail_decode(*_args: object, **_kwargs: object) -> None:
        raise PipelineError("ffmpeg exited with 1; corrupt tail")

    monkeypatch.setattr(media_validation.runner, "run", fail_decode)

    with pytest.raises(PipelineError, match="output.decode.*corrupt tail"):
        media_validation.require_output_decode(output)


def test_require_output_decode_forwards_progress_and_maps_all_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "valid.mp4"
    output.touch()
    events: list[object] = []
    observed_args: list[str] = []
    sentinel = object()

    def pass_decode(command, **kwargs) -> None:  # type: ignore[no-untyped-def]
        observed_args.extend(command.args)
        callback = kwargs["on_event"]
        callback(sentinel)

    monkeypatch.setattr(media_validation.runner, "run", pass_decode)

    media_validation.require_output_decode(output, on_event=events.append)

    assert events == [sentinel]
    assert observed_args[observed_args.index("-map") + 1] == "0:v:0"
    assert "0:a?" in observed_args
    assert observed_args[-2:] == ["null", media_validation.os.devnull]


def test_media_contract_treats_missing_and_und_language_as_unspecified(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path).model_copy(update={
        "audio": [_src(tmp_path).audio[0].model_copy(update={"language": None})],
    })
    plan = _plan(source, [])
    output_meta = source.model_copy(update={
        "path": tmp_path / "out.mp4",
        "audio": [source.audio[0].model_copy(update={"language": "und"})],
    })
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    assert media_validation.inspect_output_contract(plan, output_meta.path).valid


def test_media_contract_detects_shifted_first_video_timestamp(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path)
    plan = _plan(source, [])
    output_meta = source.model_copy(update={"path": tmp_path / "shifted.mp4"})
    output_meta._first_video_pts_sec = 1.021
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert {failure.code for failure in report.failures} == {"timeline.video_start"}


def test_media_contract_detects_stream_metadata_loss(tmp_path: Path, monkeypatch) -> None:
    source = _src(tmp_path).model_copy(update={
        "audio": [AudioStream(
            index=1,
            codec="aac",
            sample_rate=48_000,
            channels=2,
            language="eng",
            title="Director commentary",
            is_default=True,
            dispositions=("default", "forced"),
        )],
    })
    plan = _plan(source, [])
    output_meta = source.model_copy(update={
        "path": tmp_path / "out.mp4",
        "audio": [source.audio[0].model_copy(update={
            "title": None,
            "dispositions": ("default",),
        })],
    })
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert {failure.code for failure in report.failures} == {
        "streams.audio.0.title",
        "streams.audio.0.dispositions",
    }


def test_media_contract_detects_hdr_depth_and_transfer_loss(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path, hdr=True)
    plan = _plan(source, [], keep_hdr=True)
    wrong_color = source.video[0].color.model_copy(update={
        "is_hdr": False,
        "transfer": "bt709",
        "bit_depth": 8,
    })
    output_meta = source.model_copy(update={
        "path": tmp_path / "out.mp4",
        "video": [source.video[0].model_copy(update={"color": wrong_color})],
    })
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert {failure.code for failure in report.failures} == {
        "color.is_hdr",
        "color.transfer",
        "color.bit_depth",
    }


def test_media_contract_requires_bt709_limited_8bit_after_tonemap(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path, hdr=True)
    plan = _plan(source, [TransformConfig(id="video.tonemap_sdr")])
    wrong_color = source.video[0].color.model_copy(update={
        "is_hdr": False,
        "transfer": "bt709",
        "primaries": "bt709",
        "space": "bt709",
        "color_range": "pc",
        "bit_depth": 10,
    })
    output_meta = source.model_copy(update={
        "path": tmp_path / "out.mp4",
        "video": [source.video[0].model_copy(update={"color": wrong_color})],
    })
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert {failure.code for failure in report.failures} == {
        "color.color_range",
        "color.bit_depth",
    }


def test_mov_contract_accounts_for_unrepresentable_forced_disposition(
    tmp_path: Path, monkeypatch,
) -> None:
    source = _src(tmp_path).model_copy(update={
        "audio": [AudioStream(
            index=1,
            codec="aac",
            sample_rate=48_000,
            channels=2,
            is_default=True,
            dispositions=("default", "forced"),
        )],
    })
    plan = _plan(source, [], output_container="mov")
    output_meta = source.model_copy(update={
        "path": tmp_path / "out.mov",
        "container": "mov",
        "audio": [source.audio[0].model_copy(update={"dispositions": ("default",)})],
    })
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    assert media_validation.inspect_output_contract(plan, output_meta.path).valid
