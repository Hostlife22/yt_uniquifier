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
