"""BatchWorker — directory iteration with per-file signals."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from yt_uniquifier.core.models import Plan, Profile, TransformConfig
from yt_uniquifier.gui.workers.batch_worker import BatchWorker


def _make_profile() -> Profile:
    return Profile(name="t", transforms=[TransformConfig(id="video.crop_resize")])


def _make_plan(src: Path) -> Plan:
    from tests.unit.test_pipeline_graph import _plan, _src
    return _plan(_src(src.parent), [TransformConfig(id="video.crop_resize")])


def test_batch_worker_no_files_emits_failed(tmp_path: Path) -> None:
    worker = BatchWorker(
        tmp_path, tmp_path / "out", _make_profile(), None,
    )
    received_fail: list[str] = []
    worker.failed.connect(received_fail.append)
    worker.run()  # invoke directly — synchronous in test
    assert received_fail and "no files matched" in received_fail[0]


def test_batch_worker_happy_path_3_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for i in range(3):
        (input_dir / f"f{i}.mp4").touch()

    started: list[str] = []
    done: list[tuple[str, str]] = []
    fake_plan = _make_plan(tmp_path)
    with (
        patch("yt_uniquifier.gui.workers.batch_worker.build_plan", return_value=fake_plan),
        patch("yt_uniquifier.gui.workers.batch_worker.run_full"),
    ):
        worker = BatchWorker(
            input_dir, tmp_path / "out", _make_profile(), None,
        )
        worker.file_started.connect(started.append)
        worker.file_done.connect(lambda p, o: done.append((p, o)))
        worker.run()
    assert len(started) == 3
    assert len(done) == 3


def test_batch_worker_continue_on_error(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "a.mp4").touch()
    (input_dir / "b.mp4").touch()
    (input_dir / "c.mp4").touch()

    fake_plan = _make_plan(tmp_path)
    call_count = {"n": 0}

    def fake_run(*_a: object, **_kw: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("synthetic boom")

    failed: list[tuple[str, str]] = []
    done: list[tuple[str, str]] = []
    with (
        patch("yt_uniquifier.gui.workers.batch_worker.build_plan", return_value=fake_plan),
        patch("yt_uniquifier.gui.workers.batch_worker.run_full", side_effect=fake_run),
    ):
        worker = BatchWorker(
            input_dir, tmp_path / "out", _make_profile(), None,
            continue_on_error=True,
        )
        worker.file_failed.connect(lambda p, e: failed.append((p, e)))
        worker.file_done.connect(lambda p, o: done.append((p, o)))
        worker.run()
    assert len(failed) == 1
    assert len(done) == 2


def test_batch_worker_stop_on_error(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "a.mp4").touch()
    (input_dir / "b.mp4").touch()

    fake_plan = _make_plan(tmp_path)
    with (
        patch("yt_uniquifier.gui.workers.batch_worker.build_plan", return_value=fake_plan),
        patch(
            "yt_uniquifier.gui.workers.batch_worker.run_full",
            side_effect=RuntimeError("boom"),
        ),
    ):
        worker = BatchWorker(
            input_dir, tmp_path / "out", _make_profile(), None,
            continue_on_error=False,
        )
        failed_signals: list[str] = []
        worker.failed.connect(failed_signals.append)
        worker.run()
    assert failed_signals and "stopped at" in failed_signals[0]


def test_batch_worker_cancel_midway(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "a.mp4").touch()
    (input_dir / "b.mp4").touch()

    fake_plan = _make_plan(tmp_path)
    started: list[str] = []
    done: list[tuple[str, str]] = []

    def fake_run(*_a: object, **_kw: object) -> None:
        worker.request_cancel()

    with (
        patch("yt_uniquifier.gui.workers.batch_worker.build_plan", return_value=fake_plan),
        patch("yt_uniquifier.gui.workers.batch_worker.run_full", side_effect=fake_run),
    ):
        worker = BatchWorker(
            input_dir, tmp_path / "out", _make_profile(), None,
        )
        worker.file_started.connect(started.append)
        worker.file_done.connect(lambda p, o: done.append((p, o)))
        worker.run()
    # First file processed; second skipped due to cancel check at loop top.
    assert len(started) == 1
