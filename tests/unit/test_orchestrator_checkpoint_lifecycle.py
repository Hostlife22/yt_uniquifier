"""Regression tests for orchestrator checkpoint ownership and cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from yt_uniquifier.core import orchestrator
from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)


def _plan(tmp_path: Path) -> Plan:
    source = tmp_path / "input.mp4"
    source.touch()
    return Plan(
        source=SourceMeta(
            path=source,
            container="mp4",
            duration_sec=10.0,
            size_bytes=1,
            video=[
                VideoStream(
                    index=0,
                    codec="h264",
                    width=320,
                    height=180,
                    fps=24.0,
                    duration_sec=10.0,
                    pix_fmt="yuv420p",
                    color=HDRInfo(is_hdr=False),
                )
            ],
            audio=[AudioStream(index=1, codec="aac", sample_rate=48_000, channels=2)],
        ),
        profile=Profile(name="test"),
        encoder=EncoderCandidate(
            name="libx264",
            vendor="x264",
            codec="h264",
            works=True,
        ),
        plan_hash="checkpoint-lifecycle",
        run_seed=0,
    )


def _options(tmp_path: Path, *, force_new_variant: bool = False) -> orchestrator.RunOptions:
    return orchestrator.RunOptions(
        work_dir=tmp_path / "work",
        output=tmp_path / "output.mp4",
        enforce_preflight=False,
        force_new_variant=force_new_variant,
    )


def test_new_variant_does_not_archive_state_before_lock_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected concurrent run must not mutate the active run's state."""
    options = _options(tmp_path, force_new_variant=True)
    options.work_dir.mkdir()
    state_path = options.work_dir / "state.json"
    state_path.write_text('{"active": true}\n', encoding="utf-8")

    class BusyStore:
        def __init__(self, work_dir: Path, plan: Plan) -> NoReturn:
            del work_dir, plan
            raise CheckpointError("work directory is already in use")

    monkeypatch.setattr(orchestrator, "preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "CheckpointStore", BusyStore)

    with pytest.raises(CheckpointError, match="already in use"):
        orchestrator._run_full_impl(_plan(tmp_path), options, lambda event: None, None)

    assert state_path.read_text(encoding="utf-8") == '{"active": true}\n'
    assert not list(options.work_dir.glob("state.json.stale-variant-*"))


def test_store_is_closed_when_checkpoint_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failures while loading state must release the process lock immediately."""
    closed = False

    class FailingStore:
        def __init__(self, work_dir: Path, plan: Plan) -> None:
            del work_dir, plan

        def init_or_resume(self, segments: object) -> NoReturn:
            del segments
            raise CheckpointError("invalid state")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(orchestrator, "preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "plan_segments", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "CheckpointStore", FailingStore)

    with pytest.raises(CheckpointError, match="invalid state"):
        orchestrator._run_full_impl(
            _plan(tmp_path),
            _options(tmp_path),
            lambda event: None,
            None,
        )

    assert closed
