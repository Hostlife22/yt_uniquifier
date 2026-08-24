"""v1.3.0 Task 30 — watermark guardrail unit tests.

Covers:

  * graceful degradation when OpenCV isn't installed (detector returns None)
  * preflight short-circuits on --accept-watermark-risk (info finding)
  * preflight short-circuits on profile-level skip_watermark_check
  * preflight emits 'watermark.unavailable' info when detector returns None
  * preflight emits 'watermark.detected' fail when detector reports a match
  * preflight emits no finding when detector reports no match
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from yt_uniquifier.core import preflight as pf
from yt_uniquifier.core.guardrails import watermark as wm
from yt_uniquifier.core.guardrails.watermark import WatermarkFinding
from yt_uniquifier.core.models import (
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    SourceMeta,
    VideoStream,
)
from yt_uniquifier.core.pipeline import compute_plan_hash


def _src(tmp_path: Path) -> SourceMeta:
    sp = tmp_path / "src.mp4"
    sp.touch()
    return SourceMeta(
        path=sp, container="mp4", duration_sec=10.0, size_bytes=1000,
        video=[VideoStream(
            index=0, codec="h264", width=1920, height=1080, fps=24.0,
            duration_sec=10.0, pix_fmt="yuv420p", bit_rate=8_000_000,
            color=HDRInfo(is_hdr=False),
        )],
    )


def _plan(src: SourceMeta, *, skip_check: bool = False) -> Plan:
    profile = Profile(name="p", target_codec="h264",
                      skip_watermark_check=skip_check)
    enc = EncoderCandidate(name="libx264", vendor="x264",
                           codec="h264", works=True)
    return Plan(source=src, profile=profile, encoder=enc,
                plan_hash=compute_plan_hash(src, profile, enc))


# ---------------------------------------------------------------------------
# detector graceful degradation
# ---------------------------------------------------------------------------


def test_detect_watermark_returns_none_when_cv2_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **kw: object) -> object:
        if name == "cv2":
            raise ImportError("No module named 'cv2'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    src = tmp_path / "src.mp4"
    src.touch()
    assert wm.detect_watermark(src) is None


# ---------------------------------------------------------------------------
# preflight integration — overrides
# ---------------------------------------------------------------------------


def test_preflight_accept_watermark_risk_emits_attested_info(
    tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    plan = _plan(src)
    findings = pf._check_input_watermark(
        src, plan, accept_watermark_risk=True,
    )
    codes = [f.code for f in findings]
    assert codes == ["watermark.attested"]
    assert findings[0].severity == "info"


def test_preflight_skip_via_profile(tmp_path: Path) -> None:
    src = _src(tmp_path)
    plan = _plan(src, skip_check=True)
    findings = pf._check_input_watermark(src, plan)
    assert [f.code for f in findings] == ["watermark.skipped_by_profile"]
    assert findings[0].severity == "info"


def test_preflight_emits_unavailable_when_detector_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    plan = _plan(src)
    # detect_watermark returning None means OpenCV is missing or another
    # graceful-skip path fired.
    monkeypatch.setattr(
        "yt_uniquifier.core.guardrails.watermark.detect_watermark",
        lambda _src, **_kwargs: None,
    )
    findings = pf._check_input_watermark(src, plan)
    assert [f.code for f in findings] == ["watermark.unavailable"]
    assert findings[0].severity == "info"


def test_preflight_emits_fail_on_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    plan = _plan(src)
    monkeypatch.setattr(
        "yt_uniquifier.core.guardrails.watermark.detect_watermark",
        lambda _src, **_kwargs: WatermarkFinding(
            detected=True, confidence=0.78,
            sampled_frames=5, matched_frames=2,
        ),
    )
    findings = pf._check_input_watermark(src, plan)
    assert len(findings) == 1
    f = findings[0]
    assert f.code == "watermark.detected"
    assert f.severity == "fail"
    assert "2/5" in f.message
    assert "0.78" in f.message


def test_preflight_no_finding_when_detector_finds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = _src(tmp_path)
    plan = _plan(src)
    monkeypatch.setattr(
        "yt_uniquifier.core.guardrails.watermark.detect_watermark",
        lambda _src, **_kwargs: WatermarkFinding(
            detected=False, confidence=0.20,
            sampled_frames=5, matched_frames=0,
        ),
    )
    findings = pf._check_input_watermark(src, plan)
    assert findings == []


# ---------------------------------------------------------------------------
# template corpus is non-empty and grayscale
# ---------------------------------------------------------------------------


def test_template_corpus_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synthetic template generator must produce ≥1 templates and
    each must be 2D uint8 (the matchTemplate path requires grayscale)."""
    try:
        import cv2  # noqa: F401
        import numpy as np
    except ImportError:
        pytest.skip("opencv-python-headless not installed")
    templates = wm._load_templates(cv2_mod := __import__("cv2"))
    assert templates
    for t in templates:
        assert t.ndim == 2
        assert t.dtype == np.dtype("uint8")
    # Silence "unused" — cv2_mod is the same cv2.
    assert cv2_mod is not None


def _write_detection_frames(tmp_path: Path, *, overlay_frames: int) -> list[Path]:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    template = wm._load_templates(cv2)[0]
    paths: list[Path] = []
    rng = np.random.default_rng(7)
    for idx in range(5):
        frame = rng.integers(0, 256, size=(360, 640), dtype=np.uint8)
        if idx < overlay_frames:
            height, width = template.shape
            frame[10 : 10 + height, 640 - width - 10 : 630] = template
        path = tmp_path / f"frame_{idx:03d}.png"
        assert cv2.imwrite(str(path), frame)
        paths.append(path)
    return paths


def test_detector_requires_persistent_corner_overlay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    frames = _write_detection_frames(tmp_path, overlay_frames=1)
    monkeypatch.setattr(wm, "_extract_sample_frames", lambda *_a, **_kw: frames)

    finding = wm.detect_watermark(source, duration_sec=10.0)

    assert finding is not None
    assert finding.detected is False
    assert finding.matched_frames == 1


def test_detector_accepts_persistent_corner_overlay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    frames = _write_detection_frames(tmp_path, overlay_frames=4)
    monkeypatch.setattr(wm, "_extract_sample_frames", lambda *_a, **_kw: frames)

    finding = wm.detect_watermark(source, duration_sec=10.0)

    assert finding is not None
    assert finding.detected is True
    assert finding.matched_frames == 4
    assert finding.confidence > 0.99
