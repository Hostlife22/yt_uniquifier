"""Regression tests for orchestrator checkpoint ownership and cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from yt_uniquifier.core import orchestrator
from yt_uniquifier.core.errors import CheckpointError, PipelineError
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.runner import RunEvent


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


def test_completed_noop_revalidates_full_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    options = _options(tmp_path)
    segment = Segment(
        idx=0,
        start_sec=0.0,
        end_sec=10.0,
        status="done",
        out_path=tmp_path / "segment.mkv",
    )
    validated: list[Path] = []

    class CompletedStore:
        def output_is_valid(self, path: Path) -> bool:
            return path == options.output

    monkeypatch.setattr(
        orchestrator,
        "_validate_final_output",
        lambda _plan, output, _emit, _cancel: validated.append(output),
    )
    monkeypatch.setattr(
        orchestrator,
        "_reserve_run_disk_budget",
        lambda *_args: pytest.fail("no-op resume must not reserve disk"),
    )

    summary = orchestrator._run_full_body(
        plan,
        options,
        lambda _event: None,
        None,
        None,
        CompletedStore(),  # type: ignore[arg-type]
        [segment],
        [],
        [],
    )

    assert validated == [options.output]
    assert summary.output == options.output


def test_final_validation_orders_contract_before_decode_and_labels_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    events: list[RunEvent] = []

    def contract(_plan: Plan, _output: Path) -> None:
        calls.append("contract")

    def decode(_output: Path, **kwargs: object) -> None:
        calls.append("decode")
        callback = kwargs["on_event"]
        assert callable(callback)
        callback(RunEvent(kind="progress", payload={"out_time_us": "1000"}))

    monkeypatch.setattr(orchestrator, "require_output_contract", contract)
    monkeypatch.setattr(orchestrator, "require_output_decode", decode)

    orchestrator._validate_final_output(
        _plan(tmp_path), _options(tmp_path).output, events.append, None,
    )

    assert calls == ["contract", "decode"]
    assert events[0].payload["phase"] == "validation"
    assert events[1].kind == "progress"
    assert events[1].payload == {"out_time_us": "1000", "phase": "validation"}


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


def test_disk_reservations_release_when_run_body_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = False

    class FakeReservation:
        def release(self) -> None:
            nonlocal released
            released = True

    class FakeStore:
        def __init__(self, work_dir: Path, plan: Plan) -> None:
            del work_dir, plan

        def init_or_resume(self, segments: list[Segment]) -> list[Segment]:
            return segments

        def close(self) -> None:
            return None

    def fail_body(*args: object) -> NoReturn:
        reservations = args[-1]
        assert isinstance(reservations, list)
        reservations.append(FakeReservation())
        raise RuntimeError("injected body failure")

    monkeypatch.setattr(orchestrator, "preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        orchestrator,
        "plan_segments",
        lambda *args, **kwargs: [Segment(idx=0, start_sec=0.0, end_sec=10.0)],
    )
    monkeypatch.setattr(orchestrator, "CheckpointStore", FakeStore)
    monkeypatch.setattr(orchestrator, "_start_pause_observer", lambda *args: None)
    monkeypatch.setattr(orchestrator, "_run_full_body", fail_body)

    with pytest.raises(RuntimeError, match="injected body failure"):
        orchestrator._run_full_impl(
            _plan(tmp_path),
            _options(tmp_path),
            lambda event: None,
            None,
        )

    assert released


def test_disk_budget_scales_workspace_to_remaining_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    class FakeReservation:
        def release(self) -> None:
            return None

    class FakeFactory:
        @classmethod
        def acquire(
            cls,
            target: Path,
            run_id: str,
            required: int,
            **kwargs: object,
        ) -> FakeReservation:
            del cls, target, run_id, kwargs
            requested.append(required)
            return FakeReservation()

    class ResumeStore:
        def get_main_audio(self) -> Path:
            return tmp_path / "main_audio.m4a"

    monkeypatch.setattr(orchestrator, "DiskReservation", FakeFactory)
    monkeypatch.setattr(orchestrator, "estimate_work_bytes", lambda _source: 1_000)
    monkeypatch.setattr(orchestrator, "estimate_encoded_bytes", lambda _source: 500)
    options = orchestrator.RunOptions(
        work_dir=tmp_path / "work",
        output=tmp_path / "output.mp4",
        run_id="resume-run",
    )
    segments = [
        Segment(idx=0, start_sec=0.0, end_sec=5.0, status="done"),
        Segment(idx=1, start_sec=5.0, end_sec=10.0, status="pending"),
    ]

    reservations = orchestrator._reserve_run_disk_budget(
        _plan(tmp_path),
        options,
        segments,
        ResumeStore(),  # type: ignore[arg-type]
        None,
        lambda _event: None,
    )

    assert requested == [550, 550]
    assert len(reservations) == 2


def test_workspace_disk_budget_uses_measured_progress_and_pending_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment_path = tmp_path / "segment.mkv"
    segment_path.write_bytes(b"x" * 100)
    segments = [
        Segment(
            idx=0,
            start_sec=0.0,
            end_sec=5.0,
            status="done",
            out_path=segment_path,
        ),
        Segment(idx=1, start_sec=5.0, end_sec=10.0, status="pending"),
    ]

    class ProgressStore:
        def all_segments(self) -> list[Segment]:
            return segments

        def get_main_audio(self) -> None:
            return None

    class FakeReservation:
        def __init__(self, target: str, reserved_bytes: int) -> None:
            self.run_id = f"run:{target}"
            self.reserved_bytes = reserved_bytes
            self.requests: list[int] = []

        def resize(self, required: int, **_kwargs: object) -> None:
            self.requests.append(required)
            self.reserved_bytes = required

    workspace = FakeReservation("workspace", 1_000)
    final = FakeReservation("final output", 1_000)
    events: list[RunEvent] = []
    monkeypatch.setattr(orchestrator, "estimate_work_bytes", lambda _source: 1_000)
    monkeypatch.setattr(orchestrator, "estimate_audio_bytes", lambda _source: 100)
    monkeypatch.setattr(orchestrator, "estimate_encoded_bytes", lambda _source: 500)

    orchestrator._refresh_workspace_disk_budget(
        _plan(tmp_path),
        ProgressStore(),  # type: ignore[arg-type]
        [workspace, final],  # type: ignore[list-item]
        None,
        events.append,
    )

    assert workspace.requests == [660]
    assert final.requests == [550]
    assert events[0].payload == {
        "phase": "disk",
        "message": "updated workspace reservation to 0.00 GiB from measured progress",
        "reserved_bytes": 660,
        "target": "workspace",
    }
    assert events[1].payload == {
        "phase": "disk",
        "message": "updated final output reservation to 0.00 GiB from measured progress",
        "reserved_bytes": 550,
        "target": "final output",
    }


def test_disk_budget_releases_partial_acquisition_on_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = False
    calls = 0

    class FakeReservation:
        def release(self) -> None:
            nonlocal released
            released = True

    class RejectSecond:
        @classmethod
        def acquire(cls, *args: object, **kwargs: object) -> FakeReservation:
            nonlocal calls
            del cls, args, kwargs
            calls += 1
            if calls == 2:
                raise orchestrator.InsufficientDiskReservation(500, 500, 100)
            return FakeReservation()

    class EmptyStore:
        def get_main_audio(self) -> None:
            return None

    monkeypatch.setattr(orchestrator, "DiskReservation", RejectSecond)
    monkeypatch.setattr(orchestrator, "estimate_work_bytes", lambda _source: 1_000)
    monkeypatch.setattr(orchestrator, "estimate_encoded_bytes", lambda _source: 500)
    options = orchestrator.RunOptions(
        work_dir=tmp_path / "work",
        output=tmp_path / "output.mp4",
        run_id="rejected-run",
    )

    with pytest.raises(PipelineError, match="insufficient unreserved disk"):
        orchestrator._reserve_run_disk_budget(
            _plan(tmp_path),
            options,
            [Segment(idx=0, start_sec=0.0, end_sec=10.0)],
            EmptyStore(),  # type: ignore[arg-type]
            None,
            lambda _event: None,
        )

    assert released
