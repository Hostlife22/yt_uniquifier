"""QueueStatusWorker + QueueWorker — distributed queue UI workers."""

from __future__ import annotations

from pathlib import Path
from time import sleep
from unittest.mock import Mock, patch

from yt_uniquifier.core.models import Profile, TransformConfig
from yt_uniquifier.gui.workers.queue_status_worker import QueueStatusWorker
from yt_uniquifier.gui.workers.queue_worker import QueueWorker


def _profile() -> Profile:
    return Profile(name="t", transforms=[TransformConfig(id="video.crop_resize")])


def test_status_worker_failed_when_queue_not_initialised(tmp_path: Path) -> None:
    """Pointing at a non-init'd dir → failed signal."""
    worker = QueueStatusWorker(tmp_path / "missing")
    errors: list[str] = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors and "not initialised" in errors[0]


def test_status_worker_emits_stats_then_cancels(tmp_path: Path) -> None:
    """One stats emission, then we cancel — worker exits."""
    from yt_uniquifier.core.queue.leasing import init_queue
    init_queue(tmp_path)
    worker = QueueStatusWorker(tmp_path, poll_sec=0.1)
    received: list[dict] = []

    def grab_stats(s: dict) -> None:
        received.append(s)
        worker.request_cancel()

    worker.stats.connect(grab_stats)
    worker.run()
    assert received
    assert "pending" in received[0]


def test_queue_worker_empty_stops_when_flag(tmp_path: Path) -> None:
    """Empty queue + stop_after_empty=True → finished_ok with queue_empty."""
    from yt_uniquifier.core.queue.leasing import init_queue
    init_queue(tmp_path)
    worker = QueueWorker(
        tmp_path, _profile(), tmp_path / "out",
        stop_after_empty=True,
    )
    finished: list[dict] = []
    worker.finished_ok.connect(finished.append)
    worker.run()
    assert finished and finished[0]["reason"] == "queue_empty"


def test_queue_worker_processes_one_file(tmp_path: Path) -> None:
    """Add 1 file, mock run_full, ensure lease+release flow."""
    from yt_uniquifier.core.queue.leasing import FileQueue, init_queue
    init_queue(tmp_path)
    q = FileQueue(tmp_path)
    src = tmp_path / "src.mp4"
    src.touch()
    q.add(src)

    from tests.unit.test_pipeline_graph import _plan, _src
    fake_plan = _plan(_src(tmp_path), [TransformConfig(id="video.crop_resize")])

    leased_paths: list[str] = []
    done: list[tuple[str, str]] = []

    with (
        patch(
            "yt_uniquifier.gui.workers.queue_worker.build_plan",
            return_value=fake_plan,
        ),
        patch(
            "yt_uniquifier.gui.workers.queue_worker.run_full",
            side_effect=lambda _plan, opts, **_kwargs: opts.output.write_bytes(b"output"),
        ),
    ):
        worker = QueueWorker(
            tmp_path, _profile(), tmp_path / "out",
            stop_after_empty=True,
        )
        worker.lease_acquired.connect(leased_paths.append)
        worker.file_done.connect(lambda p, o: done.append((p, o)))
        worker.run()
    assert len(leased_paths) == 1
    assert len(done) == 1
    assert (tmp_path / "out" / "src.uniq.mp4").read_bytes() == b"output"


def test_queue_worker_heartbeats_during_encode(tmp_path: Path) -> None:
    from yt_uniquifier.core.queue.leasing import FileQueue, init_queue

    init_queue(tmp_path)
    q = FileQueue(tmp_path)
    src = tmp_path / "src.mp4"
    src.touch()
    q.add(src)

    from tests.unit.test_pipeline_graph import _plan, _src
    fake_plan = _plan(_src(tmp_path), [TransformConfig(id="video.crop_resize")])
    observed_mtimes: list[int] = []

    def delayed_run(_plan, opts, **_kwargs) -> None:
        alive = next((tmp_path / "in_progress").glob("*.alive"))
        observed_mtimes.append(alive.stat().st_mtime_ns)
        sleep(0.05)
        observed_mtimes.append(alive.stat().st_mtime_ns)
        opts.output.write_bytes(b"output")

    with (
        patch(
            "yt_uniquifier.gui.workers.queue_worker.build_plan",
            return_value=fake_plan,
        ),
        patch(
            "yt_uniquifier.gui.workers.queue_worker.run_full",
            side_effect=delayed_run,
        ),
    ):
        worker = QueueWorker(
            tmp_path,
            _profile(),
            tmp_path / "out",
            heartbeat_sec=0.01,
            stop_after_empty=True,
        )
        worker.run()

    assert len(observed_mtimes) == 2
    assert observed_mtimes[1] > observed_mtimes[0]


def test_queue_worker_periodically_reconciles_even_when_reaper_moves_nothing(
    tmp_path: Path,
) -> None:
    recover_commits = Mock(side_effect=[0, 1])
    reap_stale = Mock(return_value=0)
    heartbeat = Mock()
    lease_calls = 0
    worker = QueueWorker(
        tmp_path,
        _profile(),
        tmp_path / "out",
        poll_sec=0.0,
    )

    def lease() -> None:
        nonlocal lease_calls
        lease_calls += 1
        if lease_calls == 4:
            worker.request_cancel()
        return None

    fake_queue = Mock(
        recover_commits=recover_commits,
        reap_stale=reap_stale,
        heartbeat=heartbeat,
        lease=lease,
    )
    logs: list[str] = []
    worker.log.connect(logs.append)

    with (
        patch(
            "yt_uniquifier.gui.workers.queue_worker.FileQueue",
            return_value=fake_queue,
        ),
        patch("yt_uniquifier.gui.workers.queue_worker.sleep", return_value=None),
    ):
        worker.run()

    assert lease_calls == 4
    assert reap_stale.call_count == 1
    assert recover_commits.call_count == 2
    assert any("reconciled 1" in message for message in logs)
