"""v0.6.0 Round 2 perf regression tests (B4, B8, F8)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from yt_uniquifier.core.checkpoint import (
    DEBOUNCE_MAX_MARKS,
    DEBOUNCE_MAX_SEC,
    CheckpointStore,
)
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
from yt_uniquifier.core.queue.leasing import FileQueue, init_queue

# ============================================================== B4


def _plan(tmp_path: Path, plan_hash: str = "ababcdcd" * 2) -> Plan:
    src = tmp_path / "in.mp4"
    src.touch()
    return Plan(
        source=SourceMeta(
            path=src, container="mp4", duration_sec=10.0, size_bytes=1_000,
            video=[VideoStream(
                index=0, codec="h264", width=320, height=180, fps=24.0,
                duration_sec=10.0, pix_fmt="yuv420p",
                color=HDRInfo(is_hdr=False),
            )],
            audio=[AudioStream(
                index=1, codec="aac", sample_rate=48000, channels=2,
            )],
        ),
        profile=Profile(name="t"),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash=plan_hash,
        run_seed=0,
    )


def _segments(n: int = 20) -> list[Segment]:
    return [
        Segment(idx=i, start_sec=i, end_sec=i + 1, status="pending")
        for i in range(n)
    ]


def test_debounced_flush_skips_disk_within_threshold(tmp_path: Path) -> None:
    """B4: mark() calls under DEBOUNCE_MAX_MARKS and under
    DEBOUNCE_MAX_SEC must NOT touch disk between flushes.
    """
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())
    # init_or_resume flushed once. Get the post-init state mtime as the
    # baseline.
    state_path = store.state_path
    mtime_after_init = state_path.stat().st_mtime_ns

    # A handful of marks well below DEBOUNCE_MAX_MARKS=10.
    for i in range(5):
        store.mark(i, "done")

    # File must not have been rewritten — debounce should hold.
    mtime_after_marks = state_path.stat().st_mtime_ns
    assert mtime_after_marks == mtime_after_init, (
        "debounce window broken: state.json was rewritten despite only "
        "5 marks and <0.25 s elapsed"
    )

    store.close()


def test_debounced_flush_fires_after_threshold_count(
    tmp_path: Path,
) -> None:
    """B4: hitting exactly DEBOUNCE_MAX_MARKS forces a flush."""
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments(DEBOUNCE_MAX_MARKS + 2))
    state_path = store.state_path
    mtime_before = state_path.stat().st_mtime_ns

    for i in range(DEBOUNCE_MAX_MARKS):
        store.mark(i, "done")

    mtime_after = state_path.stat().st_mtime_ns
    assert mtime_after != mtime_before, (
        f"flush did not fire at {DEBOUNCE_MAX_MARKS} marks"
    )

    # Read back and verify all marks landed.
    raw = json.loads(state_path.read_text())
    for i in range(DEBOUNCE_MAX_MARKS):
        assert raw["segments"][i]["status"] == "done"

    store.close()


def test_debounced_flush_fires_after_time_threshold(
    tmp_path: Path,
) -> None:
    """B4: even a single mark eventually flushes once
    DEBOUNCE_MAX_SEC has elapsed."""
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())
    state_path = store.state_path

    # First mark hits debounce window — no immediate flush.
    store.mark(0, "done")
    initial_mtime = state_path.stat().st_mtime_ns

    # Sleep past the time threshold, then mark again — should flush.
    time.sleep(DEBOUNCE_MAX_SEC + 0.05)
    store.mark(1, "done")

    after_mtime = state_path.stat().st_mtime_ns
    assert after_mtime != initial_mtime, (
        f"flush did not fire after {DEBOUNCE_MAX_SEC} s elapsed"
    )

    raw = json.loads(state_path.read_text())
    assert raw["segments"][0]["status"] == "done"
    assert raw["segments"][1]["status"] == "done"

    store.close()


def test_explicit_flush_drains_debounce_buffer(tmp_path: Path) -> None:
    """B4: store.flush() forces an immediate fsync of pending marks."""
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())

    store.mark(0, "done")
    store.mark(1, "done")

    # No flush yet — but flush() forces it.
    store.flush()

    raw = json.loads(store.state_path.read_text())
    assert raw["segments"][0]["status"] == "done"
    assert raw["segments"][1]["status"] == "done"

    store.close()


def test_close_flushes_pending_marks(tmp_path: Path) -> None:
    """B4: close() must drain the debounce buffer before releasing
    the lock. Otherwise a clean shutdown would lose work."""
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())

    store.mark(0, "done")
    store.mark(1, "done")

    # close() must flush.
    store.close()

    raw = json.loads(store.state_path.read_text())
    assert raw["segments"][0]["status"] == "done"
    assert raw["segments"][1]["status"] == "done"


def test_set_loudnorm_force_flushes(tmp_path: Path) -> None:
    """B4: set_loudnorm is a phase-boundary write and force-flushes,
    so any pending marks land too."""
    from yt_uniquifier.core.transforms.audio_loudnorm import (
        LoudnormMeasurement,
    )
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())

    store.mark(0, "done")  # in debounce buffer
    store.set_loudnorm(LoudnormMeasurement(
        input_i=-23.0, input_tp=-1.5, input_lra=7.0,
        input_thresh=-33.0, target_offset=0.5,
    ))

    raw = json.loads(store.state_path.read_text())
    assert raw["segments"][0]["status"] == "done"
    assert raw["loudnorm_measurement"]["input_i"] == -23.0

    store.close()


# ============================================================== B8


def test_lease_cursor_avoids_redundant_iterdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B8: lease() must not re-iterate pending/ on every call.

    Seed N files, lease() N times, and assert that ``iterdir`` was
    called far fewer than N times — exactly twice on this layout
    (initial refresh + drained-refresh that returns nothing).
    """
    root = tmp_path / "q"
    init_queue(root)

    # Seed 5 ready files.
    pending = root / "pending"
    for i in range(5):
        (pending / f"in_{i:02d}.mp4").write_bytes(b"x")

    iterdir_calls = {"n": 0}
    original = Path.iterdir

    def counting_iterdir(self: Path):  # type: ignore[no-untyped-def]
        if self == pending:
            iterdir_calls["n"] += 1
        return original(self)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)

    q = FileQueue(root, host="cursor_test")
    leased: list[Path] = []
    # Bounded loop: exactly 5 successful leases. We don't probe past
    # the queue's end because the drained-state branch does an extra
    # refresh + None — fine semantically but noise for this counter.
    for _ in range(5):
        nxt = q.lease()
        assert nxt is not None
        leased.append(nxt)

    assert len(leased) == 5
    # Pre-B8: 5 iterdir calls (one per lease). Post-B8: exactly 1
    # because the cursor is refreshed once on lease #1 and serves
    # leases #2-5 without re-listing pending/.
    assert iterdir_calls["n"] == 1, (
        f"B8 cursor not effective: iterdir called {iterdir_calls['n']} "
        "times for 5 leases — should be exactly 1"
    )


def test_lease_cursor_handles_only_dotfiles(tmp_path: Path) -> None:
    """B8 fix: a queue containing only dotfiles must NOT recurse
    forever. Pre-fix bug was a refresh-loop that kept seeing the same
    dot-prefixed names.
    """
    root = tmp_path / "q"
    init_queue(root)
    pending = root / "pending"
    (pending / ".rename_probe").write_bytes(b"x")
    (pending / ".another_dot").write_bytes(b"x")

    q = FileQueue(root, host="dotonly")
    assert q.lease() is None  # must return cleanly, not recurse


def test_lease_cursor_picks_up_files_added_after_drain(
    tmp_path: Path,
) -> None:
    """B8: after the cursor empties, a fresh add() must be visible to
    the next lease() — i.e. the drained-state refresh sees new files."""
    root = tmp_path / "q"
    init_queue(root)
    q = FileQueue(root, host="late_adder")

    src1 = tmp_path / "first.mp4"
    src1.write_bytes(b"x")
    q.add(src1)

    a = q.lease()
    assert a is not None
    assert a.name == "first.mp4"

    # Cursor is now empty. Add a new file.
    src2 = tmp_path / "second.mp4"
    src2.write_bytes(b"x")
    q.add(src2)

    b = q.lease()
    assert b is not None
    assert b.name == "second.mp4"


# ============================================================== F8


def test_av1_vulkan_in_candidates() -> None:
    """F8 (v0.6.0 bonus): av1_vulkan must be in the candidate list so
    Vulkan-capable AMD/Intel GPUs get an AV1 path without NVENC/QSV."""
    from yt_uniquifier.core.encoder import _CANDIDATES

    names = [name for name, _, _ in _CANDIDATES]
    assert "av1_vulkan" in names, (
        "FFmpeg 8.0 introduced av1_vulkan; the candidate list must "
        "include it so detect_encoders probes for it on cold start"
    )

    # Vendor must be a recognised label so the runtime's
    # max_parallel resolver doesn't fall through to the unknown
    # default.
    av1 = next(
        (name, vendor, codec) for name, vendor, codec in _CANDIDATES
        if name == "av1_vulkan"
    )
    assert av1[1] == "vulkan"
    assert av1[2] == "av1"
