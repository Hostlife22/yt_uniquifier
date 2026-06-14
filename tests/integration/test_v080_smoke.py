"""v0.8.0 cross-cutting smoke — one sanity check per shipped feature.

Each round of v0.8.0 ships its own deep test file. This module verifies
the *integration surface*: that the features cohabit, that defaults are
backward-compatible, and that opt-in features stay opt-in.

Scope:
  * R1 plugin discovery doesn't break with no third-party plugins installed
  * R2 SQLite corpus round-trips through the v0.7 Corpus facade
  * R3 scene-mode plan_segments falls back to a single segment safely
  * R5 target_vmaf defaults preserve the v0.7 fast path
  * R6 calibrate metric dispatch routes through both code paths
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget,
    _build_evaluator,
    calibrate,
)
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
    SegmentationConfig,
    SourceMeta,
    TransformConfig,
)
from yt_uniquifier.core.qa.corpus_db import CorpusDB, CorpusEntry
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.segmenter import plan_segments

# ----- R1: plugin discovery ----------------------------------------------


def test_plugin_discovery_survives_no_third_party_plugins() -> None:
    """Importing the transforms package must not blow up with zero plugins."""
    import yt_uniquifier.core.transforms as transforms_pkg

    # ENTRY_POINT_GROUP is the public symbol third-party plugins target.
    assert hasattr(transforms_pkg, "ENTRY_POINT_GROUP")
    assert transforms_pkg.ENTRY_POINT_GROUP == "yt_uniquifier.transforms"

    # At least one built-in must register; if all 10 video transforms
    # are silently swallowed by the per-builtin try/except, this fires.
    from yt_uniquifier.core.transforms import all_ids, get
    ids = all_ids()
    assert "video.crop_resize" in ids
    assert get("video.crop_resize") is not None


# ----- R2: SQLite corpus round-trip --------------------------------------


def test_corpus_db_roundtrip(tmp_path: Path) -> None:
    """A v0.8 CorpusDB stores and retrieves an entry without legacy JSON."""
    db = CorpusDB(tmp_path / "corpus")
    try:
        e = CorpusEntry(
            id="v080-smoke-001",
            path=tmp_path / "src.mp4",
            added_at=1718323200.0,
            duration_sec=2.0,
            phash_frames=(0xDEAD, 0xBEEF, 0xCAFE),
            audio_fingerprint=(1, 2, 3, 4),
            sample_count=3,
        )
        db.add_entry(e)
        assert len(db) == 1
        round_tripped = db.lookup_by_id("v080-smoke-001")
        assert round_tripped is not None
        assert round_tripped.audio_fingerprint == e.audio_fingerprint
        assert round_tripped.phash_frames == e.phash_frames
        # Migration backup never appears when there was no legacy JSON.
        assert not any(p.name.startswith("index.json.migrated.")
                       for p in (tmp_path / "corpus").iterdir())
    finally:
        db.close()


# ----- R3: scene-mode segmenter ------------------------------------------


def _stub_plan(src: Path, segmentation: SegmentationConfig) -> Plan:
    """Build a Plan around a real (tiny_clip) source for plan_segments()."""
    profile = Profile(
        name="smoke",
        transforms=[TransformConfig(id="video.crop_resize",
                                     params={"max_strength": 0.04})],
        segmentation=segmentation,
    )
    return Plan(
        plan_hash="smokehash",
        source=SourceMeta(
            path=src, duration_sec=10.0, width=320, height=180,
            fps=24.0, has_audio=True, video_codec="h264",
            keyframes_sec=(0.0, 5.0, 10.0),
            size_bytes=16, container="mp4",
        ),
        profile=profile,
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        run_seed=0,
    )


@pytest.mark.integration
def test_scene_mode_falls_back_to_single_segment(
    tiny_clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scene mode with no detectable cuts produces one segment, not zero."""
    # Stub the scene detector so we never actually call PySceneDetect.
    # segmenter.py imports these lazily INSIDE _plan_scene_segments,
    # so we patch the source module.
    from yt_uniquifier.core import scene_detect as sd_mod

    monkeypatch.setattr(
        sd_mod, "detect_scene_boundaries",
        lambda *_a, **_kw: (),
    )
    monkeypatch.setattr(
        sd_mod, "snap_to_keyframes",
        lambda *_a, **_kw: (),
    )

    plan = _stub_plan(
        tiny_clip,
        SegmentationConfig(mode="scene", scene_threshold=27.0,
                           scene_min_length_sec=2.0),
    )
    segments = plan_segments(plan)
    assert len(segments) == 1
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec > 0.0


def test_keyframe_mode_remains_v07_default(tmp_path: Path) -> None:
    """Omitting segmentation must equal `mode='keyframe'` from v0.7."""
    profile_default = Profile(
        name="d",
        transforms=[TransformConfig(id="video.crop_resize",
                                     params={"max_strength": 0.04})],
    )
    assert profile_default.segmentation.mode == "keyframe"


# ----- R5: target_vmaf defaults preserve fast path -----------------------


def test_target_vmaf_default_is_none() -> None:
    """A profile without target_vmaf keeps the v0.7 single-encode path."""
    p = Profile(name="d", transforms=[
        TransformConfig(id="video.crop_resize", params={"max_strength": 0.04}),
    ])
    assert p.target_vmaf is None
    assert p.target_vmaf_step == 2
    assert p.target_vmaf_max_retries == 2


# ----- R6: calibrate metric dispatch -------------------------------------


def test_calibrate_metric_dispatch_routes_both_paths() -> None:
    """`_build_evaluator` returns distinct callables per metric."""
    chrom = _build_evaluator("chromaprint")
    sscd = _build_evaluator("sscd")
    assert chrom is not sscd
    # Both must satisfy the MetricEvaluator protocol — i.e. callable
    # taking (Path, Path, CancelToken | None) → float. We can't invoke
    # them without ffmpeg + fpcalc + torch, but the dispatch is the
    # surface this smoke covers.
    assert callable(chrom)
    assert callable(sscd)


def test_calibrate_default_metric_is_chromaprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting metric= keeps the v0.5/v0.7 behaviour bit-for-bit."""
    from yt_uniquifier.core.calibration import loop as loop_mod

    # Stub the encode + score side end-to-end so this stays pure-python.
    monkeypatch.setattr(loop_mod, "_cut_test_clip", lambda s, w, _: s)

    class _FakePlan:
        plan_hash = "smoke"

    monkeypatch.setattr(loop_mod, "build_plan",
                        lambda *_a, **_kw: _FakePlan())
    monkeypatch.setattr(loop_mod, "run_full", lambda *_a, **_kw: None)

    class _Q:
        value = 92.0
        metric = "vmaf"
        raw = 92.0
        note = None

    monkeypatch.setattr(loop_mod, "quality_score", lambda *_a, **_kw: _Q())

    called: list[str] = []

    class _CID:
        match_probability_self = 0.10

    def _fake_predict(*_a: object, **_kw: object) -> _CID:
        called.append("chromaprint")
        return _CID()

    monkeypatch.setattr(loop_mod, "predict", _fake_predict)

    res = calibrate(
        tmp_path / "x.mp4",
        Profile(name="smoke", transforms=[
            TransformConfig(id="video.crop_resize",
                            params={"max_strength": 0.04}),
        ]),
        CalibrationTarget(max_self_match=0.2, min_quality=88),
        work_dir=tmp_path / "w",
    )
    assert res.converged
    assert called == ["chromaprint"]


# ----- final cohesion check ----------------------------------------------


def test_cancel_token_unchanged_across_v080_surfaces() -> None:
    """CancelToken still pre-cancels and is_cancelled-reads idempotently.

    All R3/R4/R5/R6 features accept a CancelToken; if the type drifted
    (rename, new required arg) this fires before any of the
    metric-specific tests do.
    """
    ct = CancelToken()
    assert not ct.is_cancelled()
    ct.cancel()
    assert ct.is_cancelled()
    ct.cancel()  # idempotent
    assert ct.is_cancelled()
