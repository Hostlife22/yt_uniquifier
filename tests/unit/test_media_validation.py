from __future__ import annotations

from pathlib import Path

from tests.unit.test_pipeline_graph import _plan, _src
from yt_uniquifier.core import media_validation
from yt_uniquifier.core.models import AudioStream, Chapter, SourceMeta


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
