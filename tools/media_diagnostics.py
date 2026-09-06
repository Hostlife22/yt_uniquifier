"""Bounded-memory decoded timeline measurements for the production corpus.

Counts decoded frames/samples, not container estimates. Endpoint agreement is
NOT lip-sync verification: matching endpoints can still hide internal drift.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin, ffprobe_bin


def envelope_alignment(
    source: np.ndarray, output: np.ndarray, *, sample_rate: int,
    hop_sec: float = 0.01, max_lag_sec: float = 2.0,
) -> dict[str, Any]:
    """Match RMS envelopes; positive lag means output audio is delayed.

    Reports evidence, never proves editorial lip sync. Silence/periodic evidence
    is inconclusive. Array columns preserve decoder speaker order, not downmix.
    """
    if sample_rate <= 0 or not 0 < hop_sec <= 0.1 or not 0 < max_lag_sec <= 5:
        raise ValueError("invalid alignment sampling/window")
    if source.ndim != 2 or output.ndim != 2 or source.shape[1] != output.shape[1]:
        raise ValueError("source/output channel topology mismatch")
    if not np.isfinite(source).all() or not np.isfinite(output).all():
        raise ValueError("nonfinite PCM")
    hop = max(1, round(sample_rate * hop_sec))

    def envelope(data: np.ndarray) -> np.ndarray:
        count = len(data) // hop
        if count < 20:
            raise ValueError("too little audio evidence")
        return np.sqrt(np.mean(data[:count * hop].reshape(count, hop, -1) ** 2, axis=1))

    left, right = envelope(source), envelope(output)
    count = min(len(left), len(right))
    max_lag = min(round(max_lag_sec / hop_sec), count // 4)
    evidence = []
    for channel in range(source.shape[1]):
        scores = []
        for lag in range(-max_lag, max_lag + 1):
            a = left[max(0, -lag):min(count, count - lag), channel]
            b = right[max(0, lag):min(count, count + lag), channel]
            score = None
            if np.std(a) > 1e-6 and np.std(b) > 1e-6:
                score = float(np.corrcoef(a, b)[0, 1])
            scores.append((lag, score))
        finite = [(lag, score) for lag, score in scores if score is not None]
        if not finite:
            evidence.append({"channel": channel, "status": "not_verified", "lag_sec": None})
            continue
        best_lag, best_score = max(finite, key=lambda entry: entry[1])
        competitors = [score for lag, score in finite if abs(lag - best_lag) > 3]
        margin = best_score - max(competitors) if competitors else 0.0
        reliable = best_score >= 0.7 and margin >= 0.02 and abs(best_lag) < max_lag
        evidence.append({
            "channel": channel, "status": "measured" if reliable else "not_verified",
            "lag_sec": best_lag * hop / sample_rate,
            "correlation": best_score, "peak_margin": margin,
        })
    matrix = []
    for a in range(source.shape[1]):
        row: list[float | None] = []
        for b in range(output.shape[1]):
            x, y = left[:count, a], right[:count, b]
            row.append(float(np.corrcoef(x, y)[0, 1])
                       if np.std(x) > 1e-6 and np.std(y) > 1e-6 else None)
        matrix.append(row)
    return {
        "method": "per-channel RMS envelope cross-correlation",
        "resolution_sec": hop / sample_rate, "channels": evidence,
        "zero_lag_channel_matrix": matrix,
        "internal_av_sync": "NOT VERIFIED: audio retention is not editorial lip-sync proof",
        "channel_identity": "NOT VERIFIED without known speaker-labelled source markers",
    }


def compare_audio_window(
    source: Path, output: Path, *, start: float = 0, duration: float = 20,
) -> dict[str, Any]:
    """Bounded window, preserving leading silence and all channels; speed must match."""
    if not math.isfinite(start) or start < 0 or not 0 < duration <= 30:
        raise ValueError("invalid bounded review interval")
    metadata = [probe(path) for path in (source, output)]
    if any(not meta.audio for meta in metadata):
        raise ValueError("both inputs need audio")
    channels = metadata[0].audio[0].channels
    if channels != metadata[1].audio[0].channels:
        raise ValueError("channel count changed")
    arrays = []
    for path, meta in zip((source, output), metadata, strict=True):
        stream = meta.audio[0]
        result = subprocess.run([
            ffmpeg_bin(), "-v", "error", "-xerror", "-i", str(path),
            "-map", f"0:{stream.index}", "-af",
            f"aresample=8000:first_pts=0,atrim=start={start}:duration={duration},asetpts=N/SR/TB",
            "-t", str(duration), "-c:a", "pcm_f32le", "-f", "f32le", "-",
        ], capture_output=True, check=True, timeout=300)
        data = np.frombuffer(result.stdout, dtype="<f4")
        arrays.append(data.reshape(-1, channels))
    return {
        "start_sec": start, "duration_sec": duration,
        "layouts": [meta.audio[0].channel_layout for meta in metadata],
        "speed_policy": "same timeline required; no implicit alignment or tempo compensation",
        **envelope_alignment(arrays[0], arrays[1], sample_rate=8000),
    }


def _finite(value: str | None) -> float | None:
    try:
        result = float(value) if value is not None else None
    except ValueError:
        return None
    return result if result is not None and math.isfinite(result) else None


def decoded_timeline(path: Path, *, timeout_sec: float = 14400) -> dict[str, Any]:
    """Stream ffprobe frame records without retaining a movie in Python RAM.

    Six-decimal ffprobe timestamps have microsecond precision; integer sample
    counts retain the decoder's native sample rate and include decoded padding.
    Missing frame durations remain unknown rather than inferred from average FPS.
    """
    meta = probe(path)
    streams: dict[int, dict[str, Any]] = {}
    for video in meta.video[:1]:
        streams[video.index] = _stream("video")
    for audio in meta.audio:
        streams[audio.index] = {**_stream("audio"), "sample_rate": audio.sample_rate}
    command = [
        ffprobe_bin(), "-v", "error", "-show_frames", "-show_entries",
        "frame=stream_index,best_effort_timestamp_time,duration_time,pkt_duration_time,nb_samples",
        "-of", "compact=p=0:nk=0", str(path),
    ]
    timed_out = threading.Event()
    with tempfile.TemporaryFile(mode="w+b") as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as child:
            def expire() -> None:
                timed_out.set()
                child.kill()

            timer = threading.Timer(timeout_sec, expire)
            timer.daemon = True
            timer.start()
            try:
                assert child.stdout is not None
                for line in child.stdout:
                    fields = dict(
                        part.split("=", 1) for part in line.strip().split("|") if "=" in part
                    )
                    index = fields.get("stream_index")
                    if index is not None and index.isdigit() and int(index) in streams:
                        _add_frame(streams[int(index)], fields)
                returncode = child.wait()
            finally:
                timer.cancel()
                if child.poll() is None:
                    child.kill()
                    child.wait()
        errors.seek(0, 2)
        error_size = errors.tell()
    if timed_out.is_set():
        raise TimeoutError("decoded timeline measurement exceeded its wall limit")
    if returncode or error_size:
        raise ValueError("decoded timeline measurement reported decoder errors")
    result_streams = []
    for index, stream in streams.items():
        last_pts = stream.pop("last_pts_sec")
        last_duration = stream.pop("last_duration_sec")
        stream["end_sec"] = (
            last_pts + last_duration
            if last_pts is not None and last_duration is not None else None
        )
        stream["index"] = index
        result_streams.append(stream)
    video_end = next((s["end_sec"] for s in result_streams if s["kind"] == "video"), None)
    for stream in result_streams:
        if stream["kind"] == "audio":
            end = stream["end_sec"]
            stream["audio_minus_video_end_sec"] = (
                end - video_end if end is not None and video_end is not None else None
            )
    return {
        "method": "ffprobe_full_decode_streaming",
        "timestamp_precision_sec": 0.000001,
        "internal_av_sync": "NOT VERIFIED: endpoints do not establish content alignment",
        "streams": result_streams,
    }


def _stream(kind: str) -> dict[str, Any]:
    return {
        "kind": kind, "frames": 0, "samples": 0 if kind == "audio" else None,
        "start_sec": None, "last_pts_sec": None, "last_duration_sec": None,
        "missing_pts_frames": 0, "non_increasing_pts_frames": 0,
    }


def _add_frame(stream: dict[str, Any], fields: dict[str, str]) -> None:
    stream["frames"] += 1
    pts = _finite(fields.get("best_effort_timestamp_time"))
    if pts is None:
        stream["missing_pts_frames"] += 1
    elif stream["start_sec"] is None:
        stream["start_sec"] = pts
    previous = stream["last_pts_sec"]
    if pts is not None and previous is not None and pts <= previous:
        stream["non_increasing_pts_frames"] += 1
    duration = _finite(fields.get("duration_time"))
    if duration is None:
        duration = _finite(fields.get("pkt_duration_time"))
    if stream["kind"] == "audio":
        samples = fields.get("nb_samples", "")
        if not samples.isdigit():
            raise ValueError("decoded audio frame is missing an exact sample count")
        stream["samples"] += int(samples)
        duration = int(samples) / stream["sample_rate"]
    stream["last_pts_sec"] = pts
    stream["last_duration_sec"] = duration


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    payload = compare_audio_window(
        args.source, args.output, start=args.start, duration=args.duration,
    )
    with args.json.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
