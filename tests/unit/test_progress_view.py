"""Smoke tests for rich progress builders."""

from __future__ import annotations

from yt_uniquifier.cli.progress_view import make_batch_progress, make_run_progress


def test_run_progress_constructs() -> None:
    p = make_run_progress()
    task = p.add_task("encoding", total=100)
    assert task is not None
    p.update(task, completed=50)
    assert p.tasks[0].completed == 50


def test_batch_progress_constructs() -> None:
    p = make_batch_progress()
    t1 = p.add_task("batch", total=5)
    p.advance(t1)
    assert p.tasks[0].completed == 1
