"""A6 (v0.5.5) regression: cancel_token is honoured in calibration,
encoder detection, and QA reporting.

Pre-fix the GUI Calibrate / QA / EncoderDetect screens all had a
Cancel button that called ``worker.request_cancel()`` → set the
underlying CancelToken → but no downstream code read the token, so
work continued for minutes after the user clicked Cancel.

Post-fix:
- ``core.calibration.loop.calibrate`` checks at iteration boundary AND
  re-raises (instead of swallowing as "iteration failed") if cancel
  fires inside the inner run_full.
- ``core.encoder.detect_encoders`` checks between probe candidates.
- ``core.qa.report.build_report`` checks at each phase boundary
  (md5 / phash / audio_fp / vmaf / ssim).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yt_uniquifier.core.calibration.loop import (
    CalibrationTarget,
    calibrate,
)
from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.runner import CancelToken

# -------------------------------------------------------- calibrate

def test_calibrate_raises_on_pre_iteration_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel before the first iteration must short-circuit immediately."""
    cancel_token = CancelToken()
    cancel_token.cancel()  # pre-fire

    # Stub _cut_test_clip so we don't need ffmpeg.
    from yt_uniquifier.core.calibration import loop as loop_mod
    monkeypatch.setattr(
        loop_mod, "_cut_test_clip",
        lambda src, wd, sec: wd / "clip.mp4",
    )

    profile = Profile(name="t")
    target = CalibrationTarget()

    with pytest.raises(PipelineError, match="cancelled by user"):
        calibrate(
            input_path=tmp_path / "in.mp4",
            base_profile=profile,
            target=target,
            work_dir=tmp_path / "calib",
            cancel_token=cancel_token,
        )


def test_calibrate_reraises_cancel_from_run_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A6 core regression: when run_full raises ``PipelineError("cancelled
    by user")``, the calibrate loop must NOT swallow it as "iteration
    failed" and continue — it must re-raise."""
    cancel_token = CancelToken()
    from yt_uniquifier.core.calibration import loop as loop_mod

    monkeypatch.setattr(
        loop_mod, "_cut_test_clip",
        lambda src, wd, sec: wd / "clip.mp4",
    )
    monkeypatch.setattr(
        loop_mod, "build_plan",
        lambda inp, profile, enc: MagicMock(plan_hash="dead", profile=profile),
    )

    def fake_run_full(plan, opts, *, on_event=None, cancel_token=None):  # type: ignore[no-untyped-def]
        # Simulate run_full firing cancel mid-encode.
        if cancel_token is not None:
            cancel_token.cancel()
        raise PipelineError("cancelled by user")

    monkeypatch.setattr(loop_mod, "run_full", fake_run_full)

    profile = Profile(name="t")
    target = CalibrationTarget(max_iterations=5)

    with pytest.raises(PipelineError, match="cancelled by user"):
        calibrate(
            input_path=tmp_path / "in.mp4",
            base_profile=profile,
            target=target,
            work_dir=tmp_path / "calib",
            cancel_token=cancel_token,
        )


# -------------------------------------------------------- detect_encoders

def test_detect_encoders_raises_on_pre_call_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_token = CancelToken()
    cancel_token.cancel()

    # Force cache miss so the probe loop runs.
    from yt_uniquifier.core import encoder as enc_mod
    monkeypatch.setattr(enc_mod, "_load_cache", lambda _k, **_kw: None)
    monkeypatch.setattr(enc_mod, "_ffmpeg_version_hash", lambda: "dead")

    with pytest.raises(PipelineError, match="encoder detection cancelled"):
        detect_encoders(force=True, cancel_token=cancel_token)


def test_detect_encoders_completes_normally_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No regression: callers that don't pass a token still work."""
    from yt_uniquifier.core import encoder as enc_mod

    monkeypatch.setattr(enc_mod, "_load_cache", lambda _k, **_kw: None)
    monkeypatch.setattr(enc_mod, "_save_cache", lambda _k, _r, **_kw: None)
    monkeypatch.setattr(enc_mod, "_ffmpeg_version_hash", lambda: "dead")
    monkeypatch.setattr(
        enc_mod, "_probe_one",
        lambda name, vendor, codec: enc_mod.EncoderCandidate(
            name=name, vendor=vendor, codec=codec, works=False,
        ),
    )
    out = detect_encoders(force=True)
    assert isinstance(out, list)


# -------------------------------------------------------- build_report

def test_build_report_raises_on_pre_call_cancel(tmp_path: Path) -> None:
    from yt_uniquifier.core.qa.report import build_report

    cancel_token = CancelToken()
    cancel_token.cancel()

    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    dst = tmp_path / "b.mp4"
    dst.write_bytes(b"x")

    with pytest.raises(PipelineError, match="QA cancelled by user"):
        build_report(
            src, dst,
            run_vmaf=False, run_ssim=False, run_audio_fp=False,
            predict_cid=False,
            cancel_token=cancel_token,
        )


def test_build_report_raises_at_phash_phase_when_cancel_fires_mid_md5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel after md5 but before phash should raise at the phash
    boundary check, not silently continue."""
    from yt_uniquifier.core.qa import report as report_mod
    cancel_token = CancelToken()

    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    dst = tmp_path / "b.mp4"
    dst.write_bytes(b"x")

    # Hook md5_file to fire cancel after the second invocation
    # (i.e. after both md5_in and md5_out have completed).
    md5_calls = {"n": 0}

    def fake_md5(path: Path) -> str:
        md5_calls["n"] += 1
        if md5_calls["n"] >= 2:
            cancel_token.cancel()
        return "deadbeef"

    monkeypatch.setattr(report_mod.hashes, "md5_file", fake_md5)

    with pytest.raises(PipelineError, match="QA cancelled by user.*phash"):
        report_mod.build_report(
            src, dst,
            run_vmaf=False, run_ssim=False, run_audio_fp=False,
            predict_cid=False,
            cancel_token=cancel_token,
        )
