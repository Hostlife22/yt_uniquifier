"""Bounded-memory decoded timeline measurements for the production corpus.

Counts decoded frames/samples, not container estimates. Endpoint agreement is
NOT lip-sync verification: matching endpoints can still hide internal drift.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.utils.ffmpeg_paths import ffprobe_bin


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
