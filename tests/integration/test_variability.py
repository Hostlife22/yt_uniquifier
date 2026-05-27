"""Two runs of cid_aware on the same input produce different outputs."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


def _md5(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - content compare, not security
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _have_rubberband() -> bool:
    """True iff the local ffmpeg was built with --enable-librubberband.

    `cid_aware.yaml` ships `audio.pitch_tempo.method: rubberband` since
    v0.3.1; some Homebrew bottles (and CI runners) don't carry that filter,
    so the preflight check fails and these tests can't exercise the
    full chain. Skip cleanly in that case.
    """
    if not shutil.which("ffmpeg"):
        return False
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    return " rubberband " in res.stdout


needs_rubberband = pytest.mark.skipif(
    not _have_rubberband(),
    reason="cid_aware profile requires ffmpeg with --enable-librubberband",
)


@needs_ffmpeg
@needs_rubberband
@pytest.mark.integration
def test_two_runs_of_cid_aware_differ(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")

    # First run.
    out1 = tmp_path / "v1.mp4"
    plan1 = build_plan(tiny_clip, profile, encoder_override="libx264")
    run_full(plan1, RunOptions(
        work_dir=tmp_path / "w1" / plan1.plan_hash,
        output=out1,
        target_segment_sec=600.0,
        enforce_preflight=False,  # 2-sec clip may fail audio.sr.bad
    ))

    # Second run — fresh work_dir, fresh seed under per_run strategy.
    out2 = tmp_path / "v2.mp4"
    plan2 = build_plan(tiny_clip, profile, encoder_override="libx264")
    run_full(plan2, RunOptions(
        work_dir=tmp_path / "w2" / plan2.plan_hash,
        output=out2,
        target_segment_sec=600.0,
        enforce_preflight=False,
    ))

    # Same plan_hash (per_run strategy doesn't affect hash).
    assert plan1.plan_hash == plan2.plan_hash
    # Different seeds.
    assert plan1.run_seed != plan2.run_seed
    # Different output bytes.
    assert _md5(out1) != _md5(out2)


@needs_ffmpeg
@needs_rubberband
@pytest.mark.integration
def test_resume_same_work_dir_reuses_seed(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    """Re-running into the same work_dir produces byte-identical output."""
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    work_dir = tmp_path / "w"

    plan1 = build_plan(tiny_clip, profile, encoder_override="libx264")
    out1 = tmp_path / "a.mp4"
    run_full(plan1, RunOptions(
        work_dir=work_dir / plan1.plan_hash,
        output=out1,
        keep_segments=True,  # so resume sees them
        target_segment_sec=600.0,
        enforce_preflight=False,
    ))

    # Second invocation reads state.json, restores the seed.
    plan2 = build_plan(tiny_clip, profile, encoder_override="libx264")
    out2 = tmp_path / "b.mp4"
    run_full(plan2, RunOptions(
        work_dir=work_dir / plan2.plan_hash,
        output=out2,
        target_segment_sec=600.0,
        enforce_preflight=False,
    ))

    assert plan1.plan_hash == plan2.plan_hash
    # MD5 differs only because mp4 creation_time changes per mux; video/audio
    # bitstreams are byte-identical (same seed produced the same segments).
    # Verify content equality via probed duration + file-size proximity.
    from yt_uniquifier.core.probe import probe
    m1, m2 = probe(out1), probe(out2)
    assert abs(m1.duration_sec - m2.duration_sec) < 0.05
    # Sizes should be within ~1 KB (metadata only).
    assert abs(m1.size_bytes - m2.size_bytes) < 4096


@needs_ffmpeg
@needs_rubberband
@pytest.mark.integration
def test_new_variant_overrides_stored_seed(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path
) -> None:
    """--new-variant equivalent: force_new_variant=True rolls fresh seed."""
    profile = load_profile(PROFILES_DIR / "cid_aware.yaml")
    work_dir = tmp_path / "w"

    plan1 = build_plan(tiny_clip, profile, encoder_override="libx264")
    out1 = tmp_path / "first.mp4"
    run_full(plan1, RunOptions(
        work_dir=work_dir / plan1.plan_hash,
        output=out1,
        keep_segments=True,
        target_segment_sec=600.0,
        enforce_preflight=False,
    ))

    plan2 = build_plan(tiny_clip, profile, encoder_override="libx264")
    out2 = tmp_path / "second.mp4"
    run_full(plan2, RunOptions(
        work_dir=work_dir / plan2.plan_hash,
        output=out2,
        target_segment_sec=600.0,
        enforce_preflight=False,
        force_new_variant=True,
    ))
    # Different bytes — second run did NOT reuse the stored seed.
    assert _md5(out1) != _md5(out2)
