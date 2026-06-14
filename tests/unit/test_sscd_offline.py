"""v0.8.0 R4 — SSCD metric offline tests.

Runs without torch / torchvision. We exercise the public API by
injecting a stub model loader that returns deterministic unit vectors
shaped like ResNet-50 + GeM head output. That keeps the contract under
test (shape handling, cosine math, cancel timing, missing-dep error
surface, model SHA-256 verification, frame-extraction failure
propagation) without any of the multi-hundred-MB ML deps.

The real torch path is exercised by ``tests/integration/test_sscd_real_ffmpeg.py``
(marker ``ml``, skipped when ``torch`` is missing).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.qa import sscd
from yt_uniquifier.core.qa.sscd import (
    SSCDResult,
    _ensure_model_cached,
    _pairwise_cosine,
    _sha256_file,
    compute_sscd,
    sscd_band,
)
from yt_uniquifier.core.runner import CancelToken

# ---------------------------------------------------------------------------
# Stub model + helpers
# ---------------------------------------------------------------------------


class _StubTensor:
    """Minimal stand-in for the bits of ``torch.Tensor`` SSCD uses.

    We never import torch in this test file, so any real torch path
    would crash on attribute access. The compute_sscd code path uses
    ``_embed_frames`` (which DOES import torch) — to stay torch-free
    we monkey-patch ``_embed_frames`` and ``_pairwise_cosine`` directly
    instead of going through ``_default_model_loader``.
    """


def _stub_model_loader() -> Any:
    """Returns an object that is never actually invoked — we patch
    ``_embed_frames`` away below so the model isn't called."""
    return object()


def _make_clip(path: Path) -> None:
    """Create a 2 s testsrc2 clip for ffmpeg frame extraction.

    Only needed when we exercise the real ``_extract_frames`` path;
    most tests stub that out too. Imported lazily inside the test so
    a missing ffmpeg fails *that* test rather than the whole file.
    """
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=24:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True, capture_output=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# (1) cosine + result shape
# ---------------------------------------------------------------------------


def test_sscd_result_aggregates_mean_min_and_keeps_full_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compute_sscd must compute mean+min over the per-frame cosines and
    keep the full ordered list in the SSCDResult."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.touch()
    out.touch()

    # Stub frame extraction so we never need ffmpeg in this test.
    monkeypatch.setattr(
        sscd, "_extract_frames",
        lambda _src, _dest, *, frame_count: [Path(f"f{i}.png") for i in range(5)],
    )
    # Stub embedding to return paired sequences whose per-row cosines
    # we know in advance: [1.0, 0.9, 0.8, 0.5, 0.2].
    monkeypatch.setattr(
        sscd, "_embed_frames",
        lambda _model, frames: ("emb", len(frames)),
    )
    monkeypatch.setattr(
        sscd, "_pairwise_cosine",
        lambda _a, _b: [1.0, 0.9, 0.8, 0.5, 0.2],
    )

    res = compute_sscd(src, out, model_loader=_stub_model_loader)
    assert isinstance(res, SSCDResult)
    assert res.per_frame == (1.0, 0.9, 0.8, 0.5, 0.2)
    assert res.mean_similarity == pytest.approx(sum([1.0, 0.9, 0.8, 0.5, 0.2]) / 5)
    assert res.min_similarity == 0.2


def test_sscd_pairs_on_shorter_frame_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Asymmetric frame counts (e.g. source had fewer thumbnails than
    output) pair on the shorter list — same approach as ``phash.compare``."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.touch()
    out.touch()

    calls: list[int] = []

    def fake_extract(_src: Path, _dest: Path, *, frame_count: int) -> list[Path]:
        calls.append(frame_count)
        # First call (source) returns 3 frames; second (output) returns 5.
        n = 3 if len(calls) == 1 else 5
        return [Path(f"x{i}.png") for i in range(n)]

    monkeypatch.setattr(sscd, "_extract_frames", fake_extract)
    monkeypatch.setattr(sscd, "_embed_frames", lambda _m, frames: ("emb", len(frames)))

    seen_pair_lengths: list[int] = []

    def fake_cosine(a: Any, b: Any) -> list[float]:
        # By the time we get here both embeddings should be sliced to 3.
        seen_pair_lengths.append(a[1])
        seen_pair_lengths.append(b[1])
        return [0.5] * a[1]

    monkeypatch.setattr(sscd, "_pairwise_cosine", fake_cosine)

    res = compute_sscd(src, out, frame_count=5, model_loader=_stub_model_loader)
    assert seen_pair_lengths == [3, 3]
    assert len(res.per_frame) == 3


# ---------------------------------------------------------------------------
# (2) cancel timing
# ---------------------------------------------------------------------------


def test_cancel_before_model_load_aborts_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.touch()
    out.touch()
    token = CancelToken()
    token.cancel()

    loader_called: list[bool] = []

    def loader() -> Any:
        loader_called.append(True)
        return object()

    with pytest.raises(PipelineError, match="cancelled.*model_load"):
        compute_sscd(src, out, cancel_token=token, model_loader=loader)
    assert loader_called == []


def test_cancel_between_extractions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancel raised AFTER source extract but BEFORE output extract: the
    output extract must not run."""
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.touch()
    out.touch()

    extract_calls: list[Path] = []
    token = CancelToken()

    def fake_extract(srcp: Path, _dest: Path, *, frame_count: int) -> list[Path]:
        extract_calls.append(srcp)
        # Cancel after the source extract; the cancel-check before
        # ``extract_output`` must fire.
        if srcp == src:
            token.cancel()
        return [Path("frame.png")]

    monkeypatch.setattr(sscd, "_extract_frames", fake_extract)

    with pytest.raises(PipelineError, match="cancelled.*extract_output"):
        compute_sscd(
            src, out,
            cancel_token=token,
            model_loader=_stub_model_loader,
        )
    # Only the source extract ran.
    assert extract_calls == [src]


# ---------------------------------------------------------------------------
# (3) missing source / output paths
# ---------------------------------------------------------------------------


def test_missing_source_raises(tmp_path: Path) -> None:
    out = tmp_path / "out.mp4"
    out.touch()
    with pytest.raises(PipelineError, match="source does not exist"):
        compute_sscd(tmp_path / "nope.mp4", out, model_loader=_stub_model_loader)


def test_missing_output_raises(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    src.touch()
    with pytest.raises(PipelineError, match="output does not exist"):
        compute_sscd(src, tmp_path / "nope.mp4", model_loader=_stub_model_loader)


# ---------------------------------------------------------------------------
# (4) missing torch surfaces as PipelineError with install hint
# ---------------------------------------------------------------------------


def test_default_model_loader_raises_install_hint_without_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **kw: object) -> object:
        if name == "torch":
            raise ImportError("No module named 'torch'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(PipelineError, match=r"install yt-uniquifier\[ml\]"):
        sscd._default_model_loader()


# ---------------------------------------------------------------------------
# (5) sha-256 verification rejects tampered model
# ---------------------------------------------------------------------------


def test_ensure_model_cached_returns_existing_file(tmp_path: Path) -> None:
    cache_path = tmp_path / "sscd_disc_mixup.torchscript.pt"
    cache_path.write_bytes(b"fake weights")
    # Existing file is returned as-is (no SHA check on a pre-existing
    # download — we only verify what WE downloaded so a user who
    # vendored their own copy isn't blocked).
    assert _ensure_model_cached(tmp_path) == cache_path


def test_ensure_model_cached_sha_mismatch_deletes_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A corrupted download must be removed AND raise — silently using
    unverified weights would defeat the whole point of pinning a hash."""
    bad_path = tmp_path / "sscd_disc_mixup.torchscript.pt.partial"

    def fake_urlretrieve(_url: str, dest: str) -> None:
        Path(dest).write_bytes(b"corrupted")

    monkeypatch.setattr(sscd.urllib.request, "urlretrieve", fake_urlretrieve)

    with pytest.raises(PipelineError, match="SHA-256 mismatch"):
        _ensure_model_cached(tmp_path)
    assert not bad_path.exists()
    assert not (tmp_path / "sscd_disc_mixup.torchscript.pt").exists()


def test_sha256_helper_matches_hashlib(tmp_path: Path) -> None:
    payload = b"some bytes here" * 10_000
    fp = tmp_path / "blob.bin"
    fp.write_bytes(payload)
    assert _sha256_file(fp) == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# (6) band thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "high"),
        (0.85, "high"),
        (0.8499, "caution"),
        (0.65, "caution"),
        (0.6499, "clean"),
        (0.0, "clean"),
        (-0.1, "clean"),  # numerical safety case
    ],
)
def test_sscd_band_thresholds(value: float, expected: str) -> None:
    assert sscd_band(value) == expected


# ---------------------------------------------------------------------------
# (7) integration with build_report — opt-in flag, graceful failure
# ---------------------------------------------------------------------------


def test_build_report_does_not_call_sscd_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default ``compute_sscd=False`` path must not import or call
    the SSCD module — otherwise a fresh install without ``[ml]`` would
    eat startup latency on every report."""
    from yt_uniquifier.core.qa import report as qa_report

    invoked = []
    monkeypatch.setattr(
        sscd, "compute_sscd",
        lambda *a, **kw: (invoked.append(True), None)[1],
    )
    # We stub everything else build_report touches so we don't need real
    # ffmpeg; the assertion is on whether sscd.compute_sscd ran.
    monkeypatch.setattr(qa_report.hashes, "md5_file", lambda _p: "x")

    class _MockPh:
        samples = 0
        distance_min = 0
        distance_mean = 0.0
        distance_max = 0
        similarity = 1.0

    monkeypatch.setattr(qa_report.phash, "compare", lambda *_a, **_kw: _MockPh())

    class _MockMeta:
        size_bytes = 1
        duration_sec = 1.0
        video: list[Any] = []

    monkeypatch.setattr(qa_report, "probe_file", lambda _p: _MockMeta())
    monkeypatch.setattr(qa_report, "cid_predict", type("X", (), {
        "predict": staticmethod(lambda *_a, **_kw: type("Y", (), {
            "match_probability_self": 0.0,
            "weakest_chunk": None,
            "chunks": [],
            "corpus_matches": [],
        })()),
    }))

    src = tmp_path / "a.mp4"
    out = tmp_path / "b.mp4"
    src.touch()
    out.touch()

    report = qa_report.build_report(
        src, out,
        run_vmaf=False, run_ssim=False, run_audio_fp=False,
        compute_sscd=False,
    )
    assert invoked == []
    assert report.sscd_mean is None
    assert report.sscd_per_frame is None


def test_build_report_catches_sscd_exception_into_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A torch import error during SSCD compute must NOT take down the
    whole QA report — the cheap metrics are already collected and the
    user expects to see them. The failure surfaces as a note."""
    from yt_uniquifier.core.qa import report as qa_report

    def boom(*_a: object, **_kw: object) -> SSCDResult:
        raise PipelineError("SSCD requires [ml] extra — torch missing")

    monkeypatch.setattr(sscd, "compute_sscd", boom)
    # Same stubs as above to skip real subprocess work.
    monkeypatch.setattr(qa_report.hashes, "md5_file", lambda _p: "x")

    class _MockPh:
        samples = 0
        distance_min = 0
        distance_mean = 0.0
        distance_max = 0
        similarity = 1.0

    monkeypatch.setattr(qa_report.phash, "compare", lambda *_a, **_kw: _MockPh())

    class _MockMeta:
        size_bytes = 1
        duration_sec = 1.0
        video: list[Any] = []

    monkeypatch.setattr(qa_report, "probe_file", lambda _p: _MockMeta())

    src = tmp_path / "a.mp4"
    out = tmp_path / "b.mp4"
    src.touch()
    out.touch()

    caplog.set_level(logging.WARNING)
    report = qa_report.build_report(
        src, out,
        run_vmaf=False, run_ssim=False, run_audio_fp=False,
        predict_cid=False,
        compute_sscd=True,
    )
    # The opt-in metric is None ...
    assert report.sscd_mean is None
    # ... and the failure surfaces as a note so the user can debug.
    assert any("sscd:" in n and "ml" in n for n in report.notes), report.notes


def test_build_report_propagates_cancel_through_sscd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancel during SSCD must still raise — opt-in metric or not, the
    cancel button is sacred."""
    from yt_uniquifier.core.qa import report as qa_report

    def cancelled(*_a: object, **_kw: object) -> SSCDResult:
        raise PipelineError("SSCD cancelled by user (during embed)")

    monkeypatch.setattr(sscd, "compute_sscd", cancelled)
    monkeypatch.setattr(qa_report.hashes, "md5_file", lambda _p: "x")

    class _MockPh:
        samples = 0
        distance_min = 0
        distance_mean = 0.0
        distance_max = 0
        similarity = 1.0

    monkeypatch.setattr(qa_report.phash, "compare", lambda *_a, **_kw: _MockPh())

    class _MockMeta:
        size_bytes = 1
        duration_sec = 1.0
        video: list[Any] = []

    monkeypatch.setattr(qa_report, "probe_file", lambda _p: _MockMeta())

    src = tmp_path / "a.mp4"
    out = tmp_path / "b.mp4"
    src.touch()
    out.touch()

    with pytest.raises(PipelineError, match="cancelled"):
        qa_report.build_report(
            src, out,
            run_vmaf=False, run_ssim=False, run_audio_fp=False,
            predict_cid=False,
            compute_sscd=True,
        )


# ---------------------------------------------------------------------------
# (8) _pairwise_cosine shape check
# ---------------------------------------------------------------------------


def test_pairwise_cosine_shape_mismatch_raises() -> None:
    """If embeddings come back with different shapes, fail loud — silent
    coercion would compute meaningless numbers."""
    pytest.importorskip("torch")
    import torch

    a = torch.zeros((3, 512))
    b = torch.zeros((4, 512))
    with pytest.raises(PipelineError, match="shape mismatch"):
        _pairwise_cosine(a, b)
