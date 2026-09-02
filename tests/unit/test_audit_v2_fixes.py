"""Regression coverage for the v2 audit fixes (4-phase remediation).

Distinct from tests/unit/test_audit_fixes.py which covers the prior
7-phase audit. The remediation here addresses:

  Phase 1 — checkpoint read-paths under lock; pipeline passthrough src_idx;
            cmd_batch empty-glob exit code 2.
  Phase 2 — orchestrator output verify; gui state corrupt-file rename;
            cmd_run unparseable-time warning; runner stricter empty-cmd.
  Phase 3 — segmenter dynamic audio passthrough; orchestrator cleanup
            error event; checkpoint pid-suffixed tmp; PlanCacheEntry
            dataclass; generate_variants completed counter.
  Phase 4 — probe raises on invalid fps; calibrate exit-code docstring;
            transforms duck-typed rng.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# -------- Phase 1 --------

def test_phase1_checkpoint_reads_acquire_lock(tmp_path: Path) -> None:
    """stored_run_seed / get_loudnorm / get_main_audio acquire _lock."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.models import Segment

    class _StubPlan:
        plan_hash = "lock_test_001"
        run_seed = 7

    store = CheckpointStore(tmp_path, _StubPlan())  # type: ignore[arg-type]
    store.init_or_resume([
        Segment(idx=0, start_sec=0, end_sec=1, status="pending"),
    ])

    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(200):
                store.stored_run_seed()
                store.get_loudnorm()
                store.get_main_audio()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(50):
                path = tmp_path / f"audio_{i}.wav"
                path.write_bytes(f"audio-{i}".encode())
                store.set_main_audio(path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert store.stored_run_seed() == 7


def test_phase1_pipeline_audio_passthrough_uses_relative_indices() -> None:
    """FFmpeg audio selectors are relative, not absolute stream indices."""
    from yt_uniquifier.core.pipeline import FilterGraph

    audio = [
        MagicMock(index=1),
        MagicMock(index=3),
        MagicMock(index=4),
    ]
    profile = MagicMock(audio_tracks="all")
    source = MagicMock(audio=audio)
    plan = MagicMock(source=source, profile=profile)

    fg = FilterGraph.__new__(FilterGraph)
    fg.plan = plan
    args = fg._build_audio_passthrough()
    map_args = [args[i + 1] for i in range(len(args)) if args[i] == "-map"]
    assert map_args == ["0:a:1?", "0:a:2?"]
    assert "-c:a:1" in args
    assert "-c:a:2" in args
    assert "-c:a:3" not in args
    assert "-c:a:4" not in args


def test_phase1_pipeline_audio_passthrough_empty_when_single_track() -> None:
    from yt_uniquifier.core.pipeline import FilterGraph

    profile = MagicMock(audio_tracks="all")
    source = MagicMock(audio=[MagicMock(index=0)])
    plan = MagicMock(source=source, profile=profile)

    fg = FilterGraph.__new__(FilterGraph)
    fg.plan = plan
    assert fg._build_audio_passthrough() == []


def test_phase1_cmd_batch_empty_glob_exits_with_code_two(tmp_path: Path) -> None:
    """`yt-uniq batch <empty-dir>` exits with code 2, not 0."""
    import typer

    from yt_uniquifier.cli.cmd_batch import batch_cmd

    (tmp_path / "empty").mkdir()
    with pytest.raises(typer.Exit) as ei:
        batch_cmd(
            inputs_dir=tmp_path / "empty",
            profile=tmp_path / "fake.yaml",
            output_dir=tmp_path / "out",
            pattern="*.mp4",
            encoder_override=None,
            work_dir=tmp_path / "work",
            fast_qa=False,
            no_qa=False,
            keep_segments=False,
            continue_on_error=True,
        )
    assert ei.value.exit_code == 2


# -------- Phase 2 --------

def test_phase2_runner_rejects_empty_command() -> None:
    """run() rejects a BuiltCommand with no args."""
    from yt_uniquifier.core import runner as runner_mod
    from yt_uniquifier.core.errors import PipelineError
    from yt_uniquifier.core.pipeline import BuiltCommand

    with pytest.raises(PipelineError, match="empty"):
        runner_mod.run(
            BuiltCommand(args=[]),
            output=Path("/tmp/out.mp4"),
        )


def test_phase2_gui_state_corrupt_file_renamed_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt state.json gets archived with .corrupt-<ts> suffix,
    not silently overwritten on next save."""
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui import state as state_mod

    monkeypatch.setattr(state_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "HISTORY_PATH", tmp_path / "history.json")

    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")

    _ = state_mod.AppState()
    corrupt = list(tmp_path.glob("state.json.corrupt-*"))
    assert corrupt, f"expected state.json to be archived: {list(tmp_path.iterdir())}"


def test_phase2_cmd_run_extract_out_time_returns_zero_on_garbage() -> None:
    """`_extract_out_time_us` returns 0 (not crash) on unparseable input,
    and the public contract is preserved."""
    from yt_uniquifier.cli.cmd_run import _extract_out_time_us
    from yt_uniquifier.core.runner import RunEvent

    assert _extract_out_time_us(RunEvent(kind="progress", payload={})) == 0
    ev = RunEvent(kind="progress", payload={"out_time_us": "abc"})
    assert _extract_out_time_us(ev) == 0
    ev = RunEvent(kind="progress", payload={"out_time_us": "1234"})
    assert _extract_out_time_us(ev) == 1234


# -------- Phase 3 --------

def test_phase3_checkpoint_flush_uses_pid_suffix(tmp_path: Path) -> None:
    """_flush writes to a pid-suffixed tmp file to avoid cross-process collisions."""
    import inspect

    from yt_uniquifier.core import checkpoint as cp_mod

    src = inspect.getsource(cp_mod.CheckpointStore._flush)
    assert "getpid" in src, "expected _flush to PID-suffix its tmp name"


def test_phase3_plan_cache_entry_is_dataclass() -> None:
    """RunScreen._plan_cache uses a structured PlanCacheEntry, not a tuple."""
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui.screens.run import PlanCacheEntry

    e = PlanCacheEntry(
        input_path=Path("/tmp/in.mp4"),
        profile_path=Path("/tmp/p.yaml"),
        encoder="libx264",
        plan=MagicMock(),
    )
    assert e.encoder == "libx264"


# -------- Phase 4 --------

def test_phase4_probe_raises_on_invalid_fps() -> None:
    """A frame rate denominator of 0 raises ProbeError, not silent 0.0."""
    from yt_uniquifier.core import probe as probe_mod
    from yt_uniquifier.core.errors import ProbeError

    with pytest.raises(ProbeError, match="frame rate"):
        probe_mod._parse_fraction("30/0")


def test_phase4_cmd_calibrate_has_exit_code_docstring() -> None:
    """cmd_calibrate.calibrate_cmd documents its exit codes."""
    import inspect

    from yt_uniquifier.cli import cmd_calibrate

    doc = inspect.getdoc(cmd_calibrate.calibrate_cmd) or ""
    assert "Exit codes" in doc or "exit code" in doc.lower()
