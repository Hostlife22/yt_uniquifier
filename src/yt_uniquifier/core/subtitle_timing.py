"""Retime canonical SRT before muxing, without version-sensitive packet BSFs."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.pipeline import BuiltCommand
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_TIMING = re.compile(r"(\d+:\d{2}:\d{2},\d{3}) --> (\d+:\d{2}:\d{2},\d{3})([^\r\n]*)")
_MAX_SRT_BYTES = 32 * 1024 * 1024


def _scaled_timestamp(value: str, rate: float) -> str:
    hours, minutes, tail = value.split(":")
    seconds, milliseconds = tail.split(",")
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise PipelineError("invalid canonical SRT timestamp")
    original = ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000
    scaled = round((original + int(milliseconds)) / rate)
    total_seconds, ms = divmod(scaled, 1000)
    total_minutes, sec = divmod(total_seconds, 60)
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02}:{minute:02}:{sec:02},{ms:03}"


def retime_canonical_srt(source: Path, output: Path, rate: float) -> None:
    """Change timing lines only; identical-looking caption text stays untouched."""
    if not math.isfinite(rate) or rate <= 0:
        raise PipelineError("subtitle playback rate must be finite and positive")
    expect_index, expect_timing = True, False
    with source.open(encoding="utf-8-sig") as reader, output.open("w", encoding="utf-8") as writer:
        for line in reader:
            if expect_timing:
                match = _TIMING.fullmatch(line.rstrip("\r\n"))
                if match is None:
                    raise PipelineError("invalid canonical SRT timing line")
                start, end, settings = match.groups()
                writer.write(
                    f"{_scaled_timestamp(start, rate)} --> {_scaled_timestamp(end, rate)}"
                    f"{settings}\n"
                )
                expect_timing = False
            else:
                writer.write(line)
                if not line.strip():
                    expect_index = True
                elif expect_index:
                    if not line.strip().isdigit():
                        raise PipelineError("invalid canonical SRT cue index")
                    expect_index, expect_timing = False, True
    if expect_timing:
        raise PipelineError("truncated canonical SRT cue")


def prepare_retimed_srt(
    source: Path, stream_index: int, work_dir: Path, rate: float, *,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
) -> Path:
    raw = work_dir / f"subtitle-{stream_index}.source.srt"
    retimed = work_dir / f"subtitle-{stream_index}.retimed.srt"
    try:
        run_ffmpeg(BuiltCommand(args=[
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-copyts",
            "-i", str(source), "-map", f"0:s:{stream_index}", "-c:s", "srt",
            "-map_chapters", "-1", "-fs", str(_MAX_SRT_BYTES), str(raw),
        ]), output=raw, on_event=on_event, cancel_token=cancel_token,
            log_path=work_dir / f"subtitle-{stream_index}.extract.log")
        if raw.stat().st_size >= _MAX_SRT_BYTES:
            raise PipelineError("subtitle extraction exceeds its bounded workspace budget")
        retime_canonical_srt(raw, retimed, rate)
    except BaseException:
        retimed.unlink(missing_ok=True)
        raise
    finally:
        raw.unlink(missing_ok=True)
    return retimed
