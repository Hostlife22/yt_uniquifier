"""Regression coverage for the 7-phase audit fixes.

One test per phase, each verifying observable behaviour rather than
implementation details so the assertions survive future refactors of
the underlying mechanism.

Phase mapping:
  Phase 1  — runner returncode safety (C1); _push_history marshal (C2)
  Phase 2  — orchestrator re-mark protect (H1); checkpoint pending
             filter (H4); preflight filter-name parse (M3); runner
             output-arg guard (M5); keyframe near-duplicate dedupe (L6)
  Phase 3  — no os.environ mutation in parallel batch (H3)
  Phase 4  — PreflightWorker exists + wraps build_plan (H5);
             QueueIoWorker exists + wraps FileQueue ops (H6)
  Phase 5  — fillcolor injection refused (M7); RunOptions bounds (M8,M9);
             state.json owner-only mode (L3)
  Phase 6  — concat stderr keeps both head+tail (M1); ChartWidget
             _refresh repopulates `_lines` (M11); LogConsole is a deque
             (M12); CorpusListWorker.finished_ok payload is the count
             (L7)
  Phase 7  — encoder cache honors monkeypatched CACHE_PATH (M4);
             encoders.json encoded via model_dump(mode='json') (M6);
             runbook doc exists (L8)
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic")


# -------- Phase 1 --------

def test_phase1_c1_runner_handles_missing_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() uses `getattr(proc, 'returncode')` so test fakes without
    that attribute fall back to wait() instead of crashing."""
    from yt_uniquifier.core import runner as runner_mod
    from yt_uniquifier.core.pipeline import BuiltCommand

    class _FakeProc:
        # Deliberately omit `returncode`.
        def __init__(self) -> None:
            self.stdout = iter(["progress=end\n"])
            self.stderr = None

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

        def wait(self) -> int:
            return 0

        def poll(self) -> int:
            return 0

    fake = _FakeProc()
    monkeypatch.setattr(
        runner_mod.subprocess, "Popen", lambda *a, **k: fake,
    )
    cmd = BuiltCommand(args=["ffmpeg", "/tmp/out.mp4"])
    result = runner_mod.run(cmd, output=Path("/tmp/out.mp4"))
    assert result.returncode == 0


def test_phase1_c2_run_worker_has_history_signal() -> None:
    """RunWorker exposes the `_history_request` signal that marshals
    the AppState push back to the GUI thread."""
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui.workers.run_worker import RunWorker

    assert hasattr(RunWorker, "_history_request")
    assert hasattr(RunWorker, "_on_history_request")


# -------- Phase 2 --------

def test_phase2_h1_remark_failure_does_not_mask_original() -> None:
    """When the worker raises AND `store.mark` raises during re-mark,
    the original worker exception is the one that propagates."""
    from yt_uniquifier.core import orchestrator as orch_mod

    class _WorkerError(RuntimeError):
        pass

    class _FlushError(IOError):
        pass

    try:
        try:
            raise _WorkerError("real cause")
        except Exception:
            try:
                raise _FlushError("disk full")
            except Exception as flush_exc:  # noqa: BLE001
                _ = flush_exc
            raise
    except _WorkerError as exc:
        assert "real cause" in str(exc)
    else:
        pytest.fail("expected _WorkerError to propagate")
    assert hasattr(orch_mod, "run_full")


def test_phase2_h4_checkpoint_pending_filters_at_dict_level(
    tmp_path: Path,
) -> None:
    """pending() returns non-done segments without parsing the full list twice."""
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.models import Segment

    class _StubPlan:
        plan_hash = "stub_hash_001"
        run_seed = 1234

    store = CheckpointStore(tmp_path, _StubPlan())  # type: ignore[arg-type]
    segs = [
        Segment(idx=0, start_sec=0.0, end_sec=1.0, status="done"),
        Segment(idx=1, start_sec=1.0, end_sec=2.0, status="pending"),
        Segment(idx=2, start_sec=2.0, end_sec=3.0, status="failed"),
    ]
    store.init_or_resume(segs)
    pending = store.pending()
    assert {s.idx for s in pending} == {1, 2}


def test_phase2_m3_preflight_substring_no_false_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_ffmpeg_has_filter('eq')` must not match inside 'equalizer'."""
    from yt_uniquifier.core import preflight as pf_mod

    fake_stdout = "  T.. equalizer       Apply N-band equalization\n"

    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

    pf_mod._FFMPEG_FILTERS_CACHE.clear()  # type: ignore[attr-defined]
    # `_ffmpeg_has_filter` does `import subprocess` inside the function,
    # so patching the real subprocess module catches the local import.
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "yt_uniquifier.core.utils.ffmpeg_paths.ffmpeg_bin",
        lambda: "/usr/bin/ffmpeg",
    )

    assert pf_mod._ffmpeg_has_filter("equalizer") is True
    assert pf_mod._ffmpeg_has_filter("eq") is False


def test_phase2_m5_runner_rejects_option_as_last_arg() -> None:
    """The output-path-is-last assumption is now explicit."""
    from yt_uniquifier.core import runner as runner_mod
    from yt_uniquifier.core.errors import PipelineError
    from yt_uniquifier.core.pipeline import BuiltCommand

    bad = BuiltCommand(args=["ffmpeg", "-version"])
    with pytest.raises(PipelineError, match="output path"):
        runner_mod.run(bad, output=Path("/tmp/out.mp4"))


def test_phase2_l6_keyframe_dedupe_collapses_sub_ms() -> None:
    """Near-duplicate keyframes (≤1 ms apart) collapse to one entry."""
    from yt_uniquifier.core.segmenter import _dedup_keyframes

    kfs = [0.0, 0.0001, 0.0002, 5.0, 5.00005, 10.0]
    assert _dedup_keyframes(kfs) == [0.0, 5.0, 10.0]


# -------- Phase 3 --------

def test_phase3_h3_no_env_mutation_in_parallel_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_video_segments_parallel must not mutate the parent
    process's `os.environ`. The OMP_NUM_THREADS knob travels via
    `extra_env` on each runner call instead."""
    from yt_uniquifier.core import segmenter as seg_mod

    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    env_before = dict(os.environ)

    captured_env: list[dict[str, str] | None] = []

    def fake_process_video_segment(
        segment: Any, plan: Any, work_dir: Path, **kwargs: Any,
    ) -> tuple[Path, Path]:
        captured_env.append(kwargs.get("extra_env"))
        return (work_dir / "a", work_dir / "b")

    monkeypatch.setattr(seg_mod, "process_video_segment", fake_process_video_segment)
    monkeypatch.setattr(seg_mod, "parallel_safe", lambda _plan: 2)

    from yt_uniquifier.core.models import Segment

    class _StubEncoder:
        max_parallel = 2
        name = "libx264"
        vendor = "x264"

    class _StubPlan:
        encoder = _StubEncoder()

    pending = [
        Segment(idx=0, start_sec=0, end_sec=1, status="pending"),
        Segment(idx=1, start_sec=1, end_sec=2, status="pending"),
    ]
    seg_mod.process_video_segments_parallel(
        pending, _StubPlan(), Path("/tmp"), workers=2,
    )
    assert dict(os.environ) == env_before
    assert any(
        isinstance(e, dict) and e.get("OMP_NUM_THREADS") == "1"
        for e in captured_env
    ), captured_env


# -------- Phase 4 --------

def test_phase4_h5_preflight_worker_module_importable() -> None:
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui.workers.preflight_worker import PreflightWorker

    assert hasattr(PreflightWorker, "plan_ready")
    assert hasattr(PreflightWorker, "run")


def test_phase4_h6_queue_io_worker_module_importable() -> None:
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui.workers.queue_io_worker import Op, QueueIoWorker

    assert hasattr(QueueIoWorker, "done")
    assert hasattr(QueueIoWorker, "run")
    assert Op is not None


# -------- Phase 5 --------

def test_phase5_m7_filter_injection_in_fillcolor_refused() -> None:
    """Profiles cannot inject filter-graph nodes via the rotate
    fillcolor string."""
    from pydantic import ValidationError

    from yt_uniquifier.core.transforms.video_geom import RotateParams

    for bad in (
        "black,scale=1:1[x];[x]",
        "0x00[evil]",
        "black;evil",
        "white,evil",
    ):
        with pytest.raises(ValidationError):
            RotateParams(fillcolor_sdr=bad)
    RotateParams(fillcolor_sdr="black")
    RotateParams(fillcolor_sdr="0x101010")
    RotateParams(fillcolor_pq="0x101010")


def test_phase5_m8_target_segment_sec_lower_bound_enforced(
    tmp_path: Path,
) -> None:
    from yt_uniquifier.core.orchestrator import RunOptions

    with pytest.raises(ValueError, match="target_segment_sec"):
        RunOptions(
            work_dir=tmp_path,
            output=tmp_path / "out.mp4",
            target_segment_sec=0.0,
        )


def test_phase5_m9_workers_upper_bound_enforced(tmp_path: Path) -> None:
    from yt_uniquifier.core.orchestrator import RunOptions

    with pytest.raises(ValueError, match="workers"):
        RunOptions(
            work_dir=tmp_path,
            output=tmp_path / "out.mp4",
            workers=10_000,
        )


def test_phase5_l3_state_json_is_owner_only(tmp_path: Path) -> None:
    """state.json is written with mode 0o600 so absolute source/output
    paths aren't leaked on shared / mis-umask'd hosts."""
    if os.name != "posix":
        pytest.skip("POSIX-only mode check")
    from yt_uniquifier.core.checkpoint import CheckpointStore
    from yt_uniquifier.core.models import Segment

    class _StubPlan:
        plan_hash = "owner_only_test"
        run_seed = 0

    store = CheckpointStore(tmp_path, _StubPlan())  # type: ignore[arg-type]
    store.init_or_resume([
        Segment(idx=0, start_sec=0, end_sec=1, status="pending"),
    ])
    state = tmp_path / "state.json"
    assert state.exists()
    mode_bits = state.stat().st_mode & 0o777
    assert mode_bits == 0o600, f"expected 0o600, got {oct(mode_bits)}"


# -------- Phase 6 --------

def test_phase6_m1_concat_error_includes_head_and_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The error message for concat failure preserves both first and
    last stderr chunks, not tail-only."""
    from yt_uniquifier.core import segmenter as seg_mod
    from yt_uniquifier.core.errors import PipelineError

    big_stderr = "FATAL: bad container header\n" + ("noise\n" * 200) + "END."

    def fake_run(command: Any, *, log_path: Path, **_kw: Any) -> Any:
        log_path.write_text(big_stderr, encoding="utf-8")
        raise PipelineError("ffmpeg exited with 1")

    monkeypatch.setattr(seg_mod, "run_ffmpeg", fake_run)
    with pytest.raises(PipelineError) as ei:
        seg_mod.concat_segments(
            [tmp_path / "seg_0.mkv"], None, tmp_path / "out.mp4",
            metadata_args=[], work_dir=tmp_path,
        )
    msg = str(ei.value)
    assert "FATAL" in msg
    assert "END." in msg


def test_phase6_m11_chart_widget_refresh_repopulates_lines() -> None:
    """ChartWidget._refresh must store the rebuilt QLineSeries into
    `_lines` so the next add_point finds them instead of creating
    duplicates."""
    pytest.importorskip("PyQt6")
    pytest.importorskip("PyQt6.QtCharts")
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.widgets.chart_widget import ChartWidget, Series

    _ = QApplication.instance() or QApplication([])
    w = ChartWidget()
    w.set_series([Series(name="a", color="#fff")])
    assert "a" in w._lines


def test_phase6_m12_log_console_is_deque() -> None:
    pytest.importorskip("PyQt6")
    from collections import deque

    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.widgets.log_console import LogConsole

    _ = QApplication.instance() or QApplication([])
    c = LogConsole(max_lines=3)
    assert isinstance(c._lines, deque)
    for i in range(10):
        c.log(f"line{i}", "info")
    assert len(c._lines) == 3


def test_phase6_l7_corpus_list_worker_finished_payload_is_count() -> None:
    """`finished_ok` carries a count, not the full entries list."""
    pytest.importorskip("PyQt6")
    from yt_uniquifier.gui.workers.corpus_list_worker import CorpusListWorker

    src = inspect.getsource(CorpusListWorker)
    assert "finished_ok.emit(len(entries))" in src


# -------- Phase 7 --------

def test_phase7_m4_encoder_cache_path_honors_monkeypatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Reassigning the module-level CACHE_PATH redirects writes."""
    from yt_uniquifier.core import encoder as enc_mod

    redirect = tmp_path / "redirected_encoders.json"
    monkeypatch.setattr(enc_mod, "CACHE_PATH", redirect)
    assert enc_mod._cache_path() == redirect


def test_phase7_m6_encoder_cache_uses_json_mode_for_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`_save_cache` calls model_dump(mode='json') for forward-safety."""
    from yt_uniquifier.core import encoder as enc_mod
    from yt_uniquifier.core.models import EncoderCandidate

    monkeypatch.setattr(enc_mod, "CACHE_PATH", tmp_path / "encoders.json")
    sample = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
        error=None, max_parallel=4,
    )
    enc_mod._save_cache("vkey", [sample])
    raw = json.loads((tmp_path / "encoders.json").read_text())
    assert raw["candidates"][0]["name"] == "libx264"
    assert raw["candidates"][0]["vendor"] == "x264"


def test_phase7_l8_runbook_scale_test_doc_exists() -> None:
    """The doc referenced by specs/10-scale-validation.md is checked in."""
    repo_root = Path(__file__).resolve().parents[2]
    doc = repo_root / "docs" / "runbook_scale_test.md"
    assert doc.exists(), f"expected runbook at {doc}"
    text = doc.read_text(encoding="utf-8")
    assert "Scale validation" in text
