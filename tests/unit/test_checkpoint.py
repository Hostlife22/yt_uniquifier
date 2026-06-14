"""Unit tests for CheckpointStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.errors import CheckpointError
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
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement


def _plan(tmp_path: Path, plan_hash: str = "abc1234567890def") -> Plan:
    src = tmp_path / "source.mp4"
    src.touch()
    source = SourceMeta(
        path=src, container="mp4", duration_sec=60.0, size_bytes=1000,
        video=[VideoStream(index=0, codec="h264", width=1920, height=1080,
                           fps=24.0, duration_sec=60.0, pix_fmt="yuv420p",
                           color=HDRInfo(is_hdr=False))],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    profile = Profile(name="t", transforms=[])
    encoder = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    return Plan(source=source, profile=profile, encoder=encoder, plan_hash=plan_hash)


def _segments() -> list[Segment]:
    return [
        Segment(idx=0, start_sec=0.0, end_sec=20.0),
        Segment(idx=1, start_sec=20.0, end_sec=40.0),
        Segment(idx=2, start_sec=40.0, end_sec=60.0),
    ]


def test_init_creates_state(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    segs = store.init_or_resume(_segments())
    assert len(segs) == 3
    assert store.state_path.exists()
    raw = json.loads(store.state_path.read_text())
    assert raw["plan_hash"] == "abc1234567890def"
    assert raw["schema_version"] == 1
    assert len(raw["segments"]) == 3


def test_resume_same_plan_loads_existing(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    store1 = CheckpointStore(tmp_path / "work", plan)
    store1.init_or_resume(_segments())
    # v1.0.1: a ``done`` segment is verified against its on-disk file
    # on resume — write a non-empty payload so the verifier accepts it.
    seg0 = tmp_path / "seg0.mkv"
    seg0.write_bytes(b"encoded segment payload")
    store1.mark(0, "done", out_path=seg0)
    # B4 (v0.6.0): mark() is debounced; force a flush so the second
    # store reads the post-mark state from disk. In production this
    # happens automatically at phase boundaries (set_loudnorm /
    # set_main_audio force-flush) or via atexit on process exit.
    store1.flush()

    store2 = CheckpointStore(tmp_path / "work", plan)
    segs = store2.init_or_resume(_segments())
    assert segs[0].status == "done"
    assert segs[1].status == "pending"


def test_different_plan_hash_invalidates(tmp_path: Path) -> None:
    work = tmp_path / "work"
    s1 = CheckpointStore(work, _plan(tmp_path, plan_hash="hash_one_xxxxxxx"))
    s1.init_or_resume(_segments())
    s1.mark(0, "done")

    s2 = CheckpointStore(work, _plan(tmp_path, plan_hash="hash_two_xxxxxxx"))
    segs = s2.init_or_resume(_segments())
    assert all(s.status == "pending" for s in segs)
    # Stale file should be set aside under a timestamped name so retries
    # in the same work_dir don't clobber prior stale snapshots.
    stale_files = list(work.glob("state.json.stale-*"))
    assert stale_files, "expected one stale snapshot file in work_dir"


def test_mark_invalid_index_raises(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store.init_or_resume(_segments())
    with pytest.raises(CheckpointError, match="out of range"):
        store.mark(99, "done")


def test_loudnorm_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store.init_or_resume(_segments())
    assert store.get_loudnorm() is None

    m = LoudnormMeasurement(
        input_i=-23.0, input_tp=-2.5, input_lra=5.5,
        input_thresh=-33.0, target_offset=0.1,
    )
    store.set_loudnorm(m)

    # Re-open from disk.
    store2 = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store2.init_or_resume(_segments())
    restored = store2.get_loudnorm()
    assert restored == m


def test_main_audio_roundtrip(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store.init_or_resume(_segments())
    assert store.get_main_audio() is None

    p = tmp_path / "work" / "main_audio.m4a"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    store.set_main_audio(p)

    store2 = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store2.init_or_resume(_segments())
    assert store2.get_main_audio() == p


def test_all_done(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "work", _plan(tmp_path))
    store.init_or_resume(_segments())
    assert not store.all_done()
    for i in range(3):
        store.mark(i, "done")
    assert store.all_done()


def test_corrupt_state_raises(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "state.json").write_text("{ this is not json")
    with pytest.raises(CheckpointError, match="unreadable"):
        CheckpointStore(work, _plan(tmp_path)).init_or_resume(_segments())


def test_resume_demotes_truncated_segment(tmp_path: Path) -> None:
    """v1.0.1 Task 3: a 0-byte ``done`` segment is demoted to pending."""
    plan = _plan(tmp_path)
    store1 = CheckpointStore(tmp_path / "work", plan)
    store1.init_or_resume(_segments())
    seg0 = tmp_path / "seg0.mkv"
    seg0.write_bytes(b"good payload")
    from yt_uniquifier.core.checkpoint import sha256_file
    store1.mark(0, "done", out_path=seg0, sha256=sha256_file(seg0))
    store1.close()

    # Truncate to 0 bytes (the failure mode the task description targets:
    # a crash that leaves an empty placeholder file on disk).
    seg0.write_bytes(b"")

    store2 = CheckpointStore(tmp_path / "work", plan)
    segs = store2.init_or_resume(_segments())
    assert segs[0].status == "pending", "0-byte segment should be demoted"


def test_resume_demotes_sha256_mismatch(tmp_path: Path) -> None:
    """v1.0.1 Task 3: a ``done`` segment whose on-disk sha256 disagrees
    with the stored digest is demoted on resume.
    """
    plan = _plan(tmp_path)
    store1 = CheckpointStore(tmp_path / "work", plan)
    store1.init_or_resume(_segments())
    seg0 = tmp_path / "seg0.mkv"
    seg0.write_bytes(b"original encoded payload")
    from yt_uniquifier.core.checkpoint import sha256_file
    store1.mark(0, "done", out_path=seg0, sha256=sha256_file(seg0))
    store1.close()

    # Corrupt the segment file in-place (e.g. partial overwrite).
    seg0.write_bytes(b"different content of same length!!")

    store2 = CheckpointStore(tmp_path / "work", plan)
    segs = store2.init_or_resume(_segments())
    assert segs[0].status == "pending", "sha256 mismatch should be demoted"


def test_resume_accepts_done_with_no_stored_sha256(tmp_path: Path) -> None:
    """v1.0.1 Task 3: pre-v1.0.1 state files have no ``sha256`` field;
    those segments must NOT be demoted just because the digest is
    absent, otherwise every upgrade would discard prior progress.
    """
    plan = _plan(tmp_path)
    store1 = CheckpointStore(tmp_path / "work", plan)
    store1.init_or_resume(_segments())
    seg0 = tmp_path / "seg0.mkv"
    seg0.write_bytes(b"legacy payload")
    # Mark done WITHOUT sha256 — emulates the pre-v1.0.1 mark() signature.
    store1.mark(0, "done", out_path=seg0)
    store1.close()

    store2 = CheckpointStore(tmp_path / "work", plan)
    segs = store2.init_or_resume(_segments())
    assert segs[0].status == "done"


def test_mark_pending_clears_stale_sha256(tmp_path: Path) -> None:
    """v1.0.1 Task 3: a ``pending`` re-mark must clear any prior sha256
    so the next ``done`` doesn't accidentally compare against the
    earlier attempt's digest.
    """
    plan = _plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    store.init_or_resume(_segments())
    seg0 = tmp_path / "seg0.mkv"
    seg0.write_bytes(b"v1")
    from yt_uniquifier.core.checkpoint import sha256_file
    store.mark(0, "done", out_path=seg0, sha256=sha256_file(seg0))
    store.mark(0, "pending")
    store.flush()

    raw = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert raw["segments"][0]["sha256"] is None
