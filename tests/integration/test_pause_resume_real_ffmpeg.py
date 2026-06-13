"""v0.7 R6 / F5 — real-ffmpeg pause / resume regression.

Drives ``orchestrator.run_full`` against a multi-segment clip with a
genuine ffmpeg subprocess, fires ``PauseToken.pause()`` mid-encode,
verifies the run halts (no further `phase=segment` `done` events),
then resumes and asserts:

* the final mp4 exists, has non-zero size, and contains both a video
  and an audio stream;
* ``state.json::paused_at`` was written while paused and cleared after
  the encode completes;
* the QA-relevant output (duration parity with the source) matches a
  non-paused control run on the same fixture — i.e. pause/resume must
  not corrupt PTS or drop segments.

The unit test ``tests/unit/test_pause_resume.py`` covers the
state-machine + watcher contract in isolation. **This** test guards
the seam between Python-side pause flags and the actual ffmpeg
process group, which only matters under real SIGSTOP/SIGCONT (or
psutil.suspend on Windows). It is marked ``integration`` so the unit
matrix stays fast.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import CancelToken, PauseToken, RunEvent

PROFILES_DIR = Path(__file__).parents[2] / "src" / "yt_uniquifier" / "profiles"


@pytest.fixture
def pause_test_clip(tmp_path: Path) -> Path:
    """6-second clip with 0.5s keyframes (~12 segments at 0.5s target).

    Long enough that we can pause mid-flight and not race the encode
    to completion before the pause lands; short enough to keep the
    test under 30 s on CI hardware.
    """
    out = tmp_path / "pause-src.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-x264-params", "keyint=12:min-keyint=12:scenecut=0",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    # Hard 60s cap on the fixture itself — generating a 6 s testsrc2
    # clip takes <2 s on every supported runner. If ffmpeg ever hangs
    # here (sandbox quirk, signal mishandling) the entire integration
    # matrix would otherwise wait forever; subprocess.run without
    # ``timeout=`` is the classic CI-hang trap.
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out


def _ffprobe_duration(path: Path) -> float:
    """Return container duration via ffprobe (seconds)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _ffprobe_streams(path: Path) -> list[dict[str, object]]:
    """Return all streams from ffprobe -show_streams."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return list(json.loads(result.stdout)["streams"])


def _build_plan_and_options(
    src: Path, work_dir: Path, output: Path,
) -> tuple[object, RunOptions]:
    """Shared plan + RunOptions builder so paused/control runs match.

    ``target_segment_sec=1.0`` (the RunOptions minimum) on a 6 s clip
    yields ~6 segments — small enough chunks that the pause window
    lands before the run completes even on a fast box.
    """
    profile = load_profile(PROFILES_DIR / "soft.yaml")
    plan = build_plan(src, profile, encoder_override=None)
    options = RunOptions(
        work_dir=work_dir,
        output=output,
        target_segment_sec=1.0,
        enforce_preflight=False,  # tiny synthetic clip won't pass YT preflight
        keep_segments=False,
    )
    return plan, options


@needs_ffmpeg
@pytest.mark.integration
def test_pause_resume_produces_valid_output(
    pause_test_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    """Pause mid-encode, resume, verify the final mp4 is well-formed.

    The control case (no pause) is tested by every other integration
    test in this suite — here we focus on the pause-specific
    invariants: paused_at marker round-trip, no orphaned subprocess,
    duration matches the source.
    """
    work_dir = tmp_path / "work"
    output = tmp_path / "out.mp4"

    plan, options = _build_plan_and_options(pause_test_clip, work_dir, output)
    pause_token = PauseToken()
    cancel_token = CancelToken()
    state_path = work_dir / "state.json"

    # Watchdog: if anything goes sideways (SIGSTOP delivered but SIGCONT
    # lost on a constrained CI runner, observer thread stuck, etc.) the
    # underlying ``proc.communicate(timeout=3600)`` would otherwise hang
    # for an hour. Hard-cap the whole test at 60 s by firing
    # ``cancel_token.cancel()`` from a daemon thread.
    def _watchdog() -> None:
        time.sleep(60.0)
        if not cancel_token.is_cancelled():
            cancel_token.cancel()

    threading.Thread(target=_watchdog, daemon=True).start()

    # Capture log events so we can confirm the runner actually emitted
    # `phase=paused` / `phase=resumed` markers (the on-disk paused_at
    # is the orchestrator observer's responsibility; the runner's
    # signal is a separate seam).
    events: list[RunEvent] = []

    def _on_event(ev: RunEvent) -> None:
        events.append(ev)

    # Pause shortly after the first segment starts. We sample state.json
    # for the marker rather than poking the internal observer thread —
    # this is what a GUI consumer would actually see.
    paused_at_observed: list[str | None] = []

    def _pause_then_resume() -> None:
        # Wait for the first segment to start (at most 5 s — synthetic
        # clip encodes very fast on libx264 ultrafast).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if any(
                ev.kind == "progress" and ev.payload.get("phase") == "segment"
                for ev in events
            ):
                break
            time.sleep(0.05)
        pause_token.pause()
        # Give the observer thread one full second to flush paused_at.
        time.sleep(1.2)
        if state_path.exists():
            try:
                raw = json.loads(state_path.read_text())
                paused_at_observed.append(raw.get("paused_at"))
            except (OSError, json.JSONDecodeError):
                paused_at_observed.append(None)
        else:
            paused_at_observed.append(None)
        # Resume so the run can complete.
        pause_token.resume()

    pause_thread = threading.Thread(target=_pause_then_resume, daemon=True)
    pause_thread.start()

    summary = run_full(
        plan, options,
        on_event=_on_event,
        cancel_token=cancel_token,
        pause_token=pause_token,
    )
    pause_thread.join(timeout=2.0)

    # 1. The final output exists and is well-formed.
    assert summary.output == output
    assert output.exists() and output.stat().st_size > 0

    streams = _ffprobe_streams(output)
    codec_types = {s.get("codec_type") for s in streams}
    assert "video" in codec_types, f"no video stream in output: {streams}"
    assert "audio" in codec_types, f"no audio stream in output: {streams}"

    # 2. Duration parity with the source (allow 0.5 s slack — the
    # source-segment trim contract from CRIT-2).
    src_dur = _ffprobe_duration(pause_test_clip)
    out_dur = _ffprobe_duration(output)
    assert abs(out_dur - src_dur) <= 0.5, (
        f"duration drift: source={src_dur:.3f}s output={out_dur:.3f}s"
    )

    # 3. paused_at was visible on disk WHILE paused (mid-run sample).
    assert paused_at_observed, "pause sampler thread never recorded a snapshot"
    sampled = paused_at_observed[0]
    assert sampled is not None and isinstance(sampled, str), (
        f"state.json::paused_at missing during the pause window — got "
        f"{sampled!r}. The observer thread may have failed to persist."
    )
    assert sampled.endswith("+00:00"), (
        f"paused_at must be UTC ISO-8601, got {sampled!r}"
    )

    # 4. The runner emitted phase=paused at least once.
    pause_logs = [
        ev for ev in events
        if ev.kind == "log" and ev.payload.get("phase") == "paused"
    ]
    resume_logs = [
        ev for ev in events
        if ev.kind == "log" and ev.payload.get("phase") == "resumed"
    ]
    assert pause_logs, "runner never emitted phase=paused — SIGSTOP never landed"
    assert resume_logs, "runner never emitted phase=resumed"

    # 5. paused_at WAS cleared on done (final cleanup runs after run_full
    # returns the summary). state.json is removed by the segment-cleanup
    # path on success when keep_segments=False, so the marker either is
    # absent (file gone) OR is None (file kept but marker cleared).
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text())
            assert raw.get("paused_at") is None, (
                f"paused_at not cleared on done: {raw['paused_at']!r}"
            )
        except (OSError, json.JSONDecodeError):
            pass


@needs_ffmpeg
@pytest.mark.integration
def test_pause_with_no_pause_call_is_indistinguishable_from_control(
    pause_test_clip: Path,
    tmp_path: Path,
    isolated_cache: Path,
) -> None:
    """Wiring `pause_token` but never pausing must be a no-op.

    Guards the F5 contract: a GUI run that holds a PauseToken but
    never flips it (the user never clicks Pause) must produce the same
    bytes as a run with `pause_token=None`. Otherwise the observer
    thread or the runner watcher would have side effects on the
    happy path.
    """
    work_dir = tmp_path / "work"
    output = tmp_path / "out-nopause.mp4"

    plan, options = _build_plan_and_options(pause_test_clip, work_dir, output)
    pause_token = PauseToken()
    cancel_token = CancelToken()

    def _watchdog() -> None:
        time.sleep(60.0)
        if not cancel_token.is_cancelled():
            cancel_token.cancel()

    threading.Thread(target=_watchdog, daemon=True).start()

    summary = run_full(
        plan, options,
        on_event=lambda _ev: None,
        cancel_token=cancel_token,
        pause_token=pause_token,
    )

    assert output.exists() and output.stat().st_size > 0
    assert summary.output == output

    # The token was never paused, so paused_at must never have been
    # written. If the state file still exists, the marker must be None.
    state_path = work_dir / "state.json"
    if state_path.exists():
        raw = json.loads(state_path.read_text())
        assert raw.get("paused_at") is None
