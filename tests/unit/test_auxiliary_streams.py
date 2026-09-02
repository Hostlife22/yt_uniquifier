from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit.test_preflight import _plan, _source
from yt_uniquifier.core import media_validation
from yt_uniquifier.core import segmenter as segmenter_mod
from yt_uniquifier.core.auxiliary_streams import (
    AuxiliaryStream,
    get_auxiliary_streams,
    set_auxiliary_streams,
)
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import SourceMeta
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import has_fail, preflight
from yt_uniquifier.core.probe import _parse_auxiliary_streams


def _attachment() -> AuxiliaryStream:
    return AuxiliaryStream(
        index=3,
        kind="attachment",
        codec="",
        codec_tag="[0][0][0][0]",
        filename="font.ttf",
        mimetype="application/x-truetype-font",
        title="Caption font",
    )


def _timecode() -> AuxiliaryStream:
    return AuxiliaryStream(
        index=2,
        kind="data",
        codec="",
        codec_tag="tmcd",
        language="eng",
        title="TimeCodeHandler",
        timecode="01:00:00:00",
    )


def _attached_pic() -> AuxiliaryStream:
    return AuxiliaryStream(
        index=2,
        kind="attached_pic",
        codec="mjpeg",
        codec_tag="[0][0][0][0]",
        title="Cover",
    )


def test_auxiliary_streams_are_internal_and_survive_model_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    set_auxiliary_streams(source, (_attachment(),))

    assert get_auxiliary_streams(source) == (_attachment(),)
    assert get_auxiliary_streams(source.model_copy()) == (_attachment(),)
    assert "auxiliary" not in source.model_dump(mode="json")


def test_auxiliary_topology_changes_plan_hash(tmp_path: Path) -> None:
    source = _source(tmp_path)
    plan = _plan(source, [], output_container="mkv")
    without_attachment = compute_plan_hash(source, plan.profile, plan.encoder)
    set_auxiliary_streams(source, (_attachment(),))

    assert compute_plan_hash(source, plan.profile, plan.encoder) != without_attachment


def test_mov_chapter_carrier_is_not_counted_as_user_data() -> None:
    chapter_carrier = {
        "index": 3,
        "codec_type": "data",
        "codec_name": "bin_data",
        "codec_tag_string": "text",
        "tags": {"handler_name": "SubtitleHandler", "language": "eng"},
    }

    assert _parse_auxiliary_streams([chapter_carrier], has_chapters=True) == ()
    assert len(_parse_auxiliary_streams([chapter_carrier], has_chapters=False)) == 1


@pytest.mark.parametrize(
    ("stream", "container", "expected_code", "fails"),
    [
        (_attachment(), "mkv", "aux.attachments.preserved", False),
        (_attachment(), "mp4", "aux.attachments.unsupported", True),
        (_timecode(), "mov", "aux.data.preserved", False),
        (_timecode(), "mp4", "aux.data.unsupported", True),
        (_timecode(), "mkv", "aux.data.unsupported", True),
        (_attached_pic(), "mp4", "aux.attached_pic.preserved", False),
        (_attached_pic(), "mov", "aux.attached_pic.unsupported", True),
        (_attached_pic(), "mkv", "aux.attached_pic.unsupported", True),
    ],
)
def test_preflight_enforces_auxiliary_container_policy(
    tmp_path: Path,
    stream: AuxiliaryStream,
    container: str,
    expected_code: str,
    fails: bool,
) -> None:
    source = _source(tmp_path)
    set_auxiliary_streams(source, (stream,))
    plan = _plan(source, [], output_container=container)

    findings = preflight(source, plan, plan.encoder)

    matching = [finding for finding in findings if finding.code == expected_code]
    assert matching
    assert has_fail(matching) is fails


def test_concat_maps_mkv_attachment_and_restores_required_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _capture(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        Path(args[-1]).write_bytes(b"muxed")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _capture)
    segment = tmp_path / "segment.mkv"
    source = tmp_path / "source.mkv"
    segment.touch()
    source.touch()

    segmenter_mod.concat_segments(
        [segment], None, tmp_path / "output.mkv", ["-map_metadata", "-1"],
        work_dir=tmp_path / "work", media_source=source,
        auxiliary_streams=[_attachment()],
    )

    maps = [captured[index + 1] for index, value in enumerate(captured) if value == "-map"]
    assert "1:3" in maps
    assert captured[
        captured.index("-c:t"):captured.index("-c:t") + 2
    ] == ["-c:t", "copy"]
    assert "-metadata:s:t:0" in captured
    assert "filename=font.ttf" in captured
    assert "mimetype=application/x-truetype-font" in captured


def test_concat_maps_mov_timecode_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _capture(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        Path(args[-1]).write_bytes(b"muxed")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(segmenter_mod.subprocess, "run", _capture)
    segment = tmp_path / "segment.mkv"
    source = tmp_path / "source.mov"
    segment.touch()
    source.touch()

    segmenter_mod.concat_segments(
        [segment], None, tmp_path / "output.mov", [],
        work_dir=tmp_path / "work", media_source=source,
        auxiliary_streams=[_timecode()],
    )

    maps = [captured[index + 1] for index, value in enumerate(captured) if value == "-map"]
    assert "1:2" in maps
    assert captured[
        captured.index("-c:d"):captured.index("-c:d") + 2
    ] == ["-c:d", "copy"]


@pytest.mark.parametrize(
    ("output_name", "stream"),
    [("output.mp4", _attachment()), ("output.mkv", _timecode())],
)
def test_concat_rejects_unsupported_auxiliary_topology_before_ffmpeg(
    tmp_path: Path,
    output_name: str,
    stream: AuxiliaryStream,
) -> None:
    segment = tmp_path / "segment.mkv"
    source = tmp_path / "source.mkv"
    segment.touch()
    source.touch()

    with pytest.raises(PipelineError, match="auxiliary stream"):
        segmenter_mod.concat_segments(
            [segment], None, tmp_path / output_name, [],
            work_dir=tmp_path / "work", media_source=source,
            auxiliary_streams=[stream],
        )


def test_media_contract_detects_missing_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    set_auxiliary_streams(source, (_attachment(),))
    plan = _plan(source, [], output_container="mkv")
    output_meta: SourceMeta = source.model_copy(update={"path": tmp_path / "out.mkv"})
    set_auxiliary_streams(output_meta, ())
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert "streams.attachment" in {failure.code for failure in report.failures}


def test_media_contract_detects_changed_timecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    set_auxiliary_streams(source, (_timecode(),))
    plan = _plan(source, [], output_container="mov")
    output_meta: SourceMeta = source.model_copy(update={"path": tmp_path / "out.mov"})
    set_auxiliary_streams(output_meta, (
        AuxiliaryStream(**{**_timecode().__dict__, "timecode": "02:00:00:00"}),
    ))
    monkeypatch.setattr(media_validation, "probe", lambda _path: output_meta)

    report = media_validation.inspect_output_contract(plan, output_meta.path)

    assert "streams.data.0.timecode" in {failure.code for failure in report.failures}


def test_multiple_program_video_streams_fail_preflight(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source = source.model_copy(update={"video": [source.video[0], source.video[0]]})
    plan = _plan(source, [])

    findings = preflight(source, plan, plan.encoder)

    assert "video.multiple_streams.unsupported" in {
        finding.code for finding in findings if finding.severity == "fail"
    }
