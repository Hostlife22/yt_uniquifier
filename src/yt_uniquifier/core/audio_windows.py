"""Plan audio processing windows for the `divergent` seed strategy.

Audio gets one rng draw per run by default, which means every video
segment shares identical audio params (rubberband pitch, Haas delay,
compand threshold, EQ band shifts). A temporal-aware audio CID has a
stable target.

The `divergent` seed strategy v0.4.2 splits audio into ~60 s windows,
each with its own seed derived via `derive_segment_seed(plan_hash,
window_idx, run_seed)`. Adjacent windows crossfade for 0.1 s.

Loudnorm is NOT windowed — it runs globally on the concatenated
post-transform stream so per-window level differences are flattened.
"""

from __future__ import annotations

from dataclasses import dataclass

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan

# 60 s windows + 0.1 s crossfade at each internal boundary.
WINDOW_SEC = 60.0
CROSSFADE_SEC = 0.1
# Audio window indices are namespaced via this offset so they don't
# collide with video segment indices in `derive_segment_seed`.
AUDIO_WINDOW_NS_OFFSET = 1_000_000


@dataclass(frozen=True)
class AudioWindow:
    idx: int
    start_sec: float
    end_sec: float
    crossfade_in_sec: float    # 0 for the first window
    crossfade_out_sec: float   # 0 for the last window


def verify_audio_filters_available(plan: Plan) -> None:
    """Re-probe ffmpeg filter availability right before the audio chain runs.

    Defense-in-depth against the 2026-05-31 matrix incident
    (`docs/bug-triage-2026-05-31.md` #2): preflight's
    `_check_pitch_rubberband` reported `rubberband` available, but
    runtime ffmpeg threw "No such filter: 'rubberband'" 18 minutes into
    an encode after all video segments completed. Root cause
    unconfirmed (likely transient cache state during a concurrent
    `brew` operation), but the cost was clear: the user burned 18 min
    of video work before the audio chain failed.

    Calling this immediately before `run_ffmpeg` on the audio chain
    closes the window between preflight and runtime. The probe takes
    ~200 ms (cached after the first call within the process) — cheap
    insurance against another multi-minute lost-work incident.

    Raises `PipelineError` with a clear remediation message when a
    required filter cannot be opened. Stays silent on the happy path.
    """
    # Lazy import: preflight imports from many places and audio_windows
    # is in the core hot-path. Avoid a top-level circular-risk dep.
    from yt_uniquifier.core.preflight import _ffmpeg_filter_works

    needs_rb = any(
        tc.enabled and tc.id == "audio.pitch_tempo"
        and (tc.params or {}).get("method") == "rubberband"
        for tc in plan.profile.transforms
    )
    if not needs_rb:
        return
    if _ffmpeg_filter_works("rubberband=pitch=1.0", "audio"):
        return
    raise PipelineError(
        "Audio chain pre-flight failed: ffmpeg cannot open the "
        "`rubberband` filter even though preflight reported it "
        "available. Profile uses audio.pitch_tempo method='rubberband' "
        "which requires ffmpeg built with --enable-librubberband. "
        "Re-install ffmpeg (Homebrew default ships it; system ffmpeg "
        "may not) or switch the profile to method='asetrate'."
    )


def plan_windows(duration_sec: float) -> list[AudioWindow]:
    """Split [0, duration_sec] into ≈ duration_sec / WINDOW_SEC pieces.

    For audio shorter than 2 × WINDOW_SEC, returns a single window
    covering the whole duration — no point splitting 30 s of audio.

    Windows tile the duration with no gaps (last window absorbs the
    fractional remainder). Crossfade flags signal where `acrossfade`
    boundaries belong.
    """
    if duration_sec < 2 * WINDOW_SEC:
        return [AudioWindow(
            idx=0, start_sec=0.0, end_sec=duration_sec,
            crossfade_in_sec=0.0, crossfade_out_sec=0.0,
        )]

    n_windows = int(duration_sec / WINDOW_SEC)
    base = duration_sec / n_windows
    windows: list[AudioWindow] = []
    for i in range(n_windows):
        start = i * base
        # Last window absorbs the remainder so the tiling stays exact.
        end = (i + 1) * base if i < n_windows - 1 else duration_sec
        cf_in = CROSSFADE_SEC if i > 0 else 0.0
        cf_out = CROSSFADE_SEC if i < n_windows - 1 else 0.0
        windows.append(AudioWindow(
            idx=i, start_sec=start, end_sec=end,
            crossfade_in_sec=cf_in, crossfade_out_sec=cf_out,
        ))
    return windows
