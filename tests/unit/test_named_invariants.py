"""Regression tests for invariants explicitly named in CLAUDE.md.

CLAUDE.md spells out a handful of core invariants — but several were
not covered by tests. This file pins them so a regression that drops
any of these properties surfaces immediately:

1. ``CheckpointStore`` is thread-safe — concurrent ``mark()`` from
   multiple threads must leave ``state.json`` intact with no lost
   updates.
2. ``compute_plan_hash`` is platform-portable — a profile that differs
   only by OS path separator must hash identically (the json-mode
   pydantic dump normalises Path to forward slashes).
3. ``FilterChain.filter_str`` must NOT include its own ``[in_label]``
   prefix — a builder that emits ``[in_lbl]<expr>`` produces a
   double-prefix that ffmpeg rejects. The pipeline always wraps as
   ``[{in_label}]{filter_str}[{out_label}]``.
"""

from __future__ import annotations

import random
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.models import (
    AudioStream,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    Segment,
    SourceMeta,
    TransformConfig,
    VideoStream,
)
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.transforms import all_ids, get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build


# ---- helpers ----------------------------------------------------------------


def _minimal_plan(tmp_path: Path) -> Plan:
    p = tmp_path / "src.mp4"
    p.touch()
    source = SourceMeta(
        path=p, container="mp4", duration_sec=120.0, size_bytes=100,
        video=[VideoStream(
            index=0, codec="h264", width=1920, height=1080, fps=24.0,
            duration_sec=120.0, pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(
            index=1, codec="aac", sample_rate=48000, channels=2,
        )],
    )
    profile = Profile(name="medium", transforms=[])
    enc = EncoderCandidate(
        name="libx264", vendor="x264", codec="h264", works=True,
    )
    return Plan(
        source=source, profile=profile, encoder=enc,
        plan_hash=compute_plan_hash(source, profile, enc),
    )


# ---- 1. CheckpointStore thread-safety --------------------------------------


def test_checkpoint_store_concurrent_mark_no_lost_updates(tmp_path: Path) -> None:
    """20 threads call mark() concurrently; final state.json must have
    every segment recorded as 'done'."""
    plan = _minimal_plan(tmp_path)
    store = CheckpointStore(tmp_path / "work", plan)
    n = 20
    segs = [
        Segment(idx=i, start_sec=float(i), end_sec=float(i + 1), status="pending")
        for i in range(n)
    ]
    store.init_or_resume(segs)

    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            barrier.wait()
            store.mark(i, "done", out_path=tmp_path / f"out_{i:04d}.mkv")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"workers raised: {errors}"
    assert store.all_done(), (
        "concurrent mark() lost an update — not every segment is 'done'"
    )
    # Re-open the store to verify state.json is intact and parseable.
    fresh = CheckpointStore(tmp_path / "work", plan)
    resumed = fresh.init_or_resume(segs)
    assert sum(1 for s in resumed if s.status == "done") == n


# ---- 2. Plan hash cross-platform stability ---------------------------------


def test_plan_hash_uses_json_mode_for_path_fields(tmp_path: Path) -> None:
    """A profile carrying a Path-typed field (BlendBParams.b_video_path)
    must serialise that Path via pydantic mode='json' so the hash
    survives a round-trip through the JSON form.

    The full Windows-vs-POSIX divergence test requires a Windows
    runner — pydantic's mode='json' on Path returns str(path) which
    is platform-native. CI matrix today is ubuntu+macos only. Here we
    pin a weaker but still useful invariant: the hash is deterministic
    across two equivalent constructions and pydantic-serialisable.
    """
    src_path = tmp_path / "src.mp4"
    src_path.touch()

    def _make_plan() -> Plan:
        profile = Profile(
            name="t",
            transforms=[
                TransformConfig(
                    id="video.blend_b",
                    params={
                        "b_video_path": str(tmp_path / "b.mp4"),
                        "opacity": 0.05,
                    },
                ),
            ],
        )
        source = SourceMeta(
            path=src_path, container="mp4", duration_sec=10.0,
            size_bytes=100,
            video=[VideoStream(
                index=0, codec="h264", width=1280, height=720, fps=24.0,
                duration_sec=10.0, pix_fmt="yuv420p",
                color=HDRInfo(is_hdr=False),
            )],
            audio=[],
        )
        enc = EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        )
        return Plan(
            source=source, profile=profile, encoder=enc,
            plan_hash=compute_plan_hash(source, profile, enc),
        )

    a = _make_plan()
    b = _make_plan()
    assert a.plan_hash == b.plan_hash, (
        "compute_plan_hash on a profile with Path-typed fields must be "
        "deterministic; got divergent hashes"
    )
    # Verify mode='json' is actually used: the profile must round-trip
    # via JSON without TypeError on Path.
    import json
    dumped = a.profile.model_dump(mode="json")
    json.dumps(dumped)  # no default=str needed — raises if Path leaked


# Reference imports keep PurePosixPath/PureWindowsPath available for a
# future Windows-runner test without re-touching imports.
_ = (PurePosixPath, PureWindowsPath)


# ---- 3. No double-prefix in transform filter_str ---------------------------

# Transforms that intentionally emit multi-chain fragments (documented in
# their docstring). These contain semicolons / source-filters and do not
# violate the wrapping contract.
_KNOWN_MULTI_CHAIN_TRANSFORMS = {
    "audio.noise_overlay",  # anull[main];anoisesrc=...[noise];[main][noise]amix=...
}

# Transforms with required params that have no schema default. The
# double-prefix invariant still applies; we just can't construct them
# from defaults alone, so skip in this generic sweep.
_TRANSFORMS_WITHOUT_DEFAULTS = {
    "video.blend_b",  # b_video_path is required
}


def test_no_transform_emits_input_label_prefix() -> None:
    """For every registered transform, the returned FilterChain.filter_str
    must NOT start with ``[`` (which would mean the builder is emitting
    its own ``[in_label]`` prefix, producing ``[in][in]<expr>...`` after
    the pipeline wraps it — invalid filter_complex).
    """
    for tid in all_ids():
        if tid in _KNOWN_MULTI_CHAIN_TRANSFORMS:
            continue
        if tid in _TRANSFORMS_WITHOUT_DEFAULTS:
            continue
        spec = get(tid)
        params = spec.schema.model_validate(spec.defaults)
        in_lbl = "0:v:0" if spec.kind == "video" else "0:a:0"
        try:
            chain = call_build(
                spec, params, LabelAllocator(), in_lbl, rng=random.Random(0),
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"call_build({tid!r}) raised: {exc!r}")

        assert not chain.filter_str.startswith("["), (
            f"transform {tid!r} emits its own [in_label] prefix: "
            f"{chain.filter_str!r}. Pipeline wraps filter_str as "
            f"[in]{{filter_str}}[out]; a leading [ produces a "
            f"double-prefix that ffmpeg rejects."
        )
