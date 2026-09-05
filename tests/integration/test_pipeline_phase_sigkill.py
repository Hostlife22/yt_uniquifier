"""Full media-phase SIGKILL/resume qualification on POSIX hosts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg

REPO = Path(__file__).resolve().parents[2]
FAULT_CHILD = REPO / "tools" / "pipeline_phase_fault_child.py"
SOFT_PROFILE = REPO / "src" / "yt_uniquifier" / "profiles" / "soft.yaml"
PHASES = (
    "after_probe",
    "after_plan",
    "after_segment",
    "during_audio",
    "during_concat",
    "during_validation",
    "after_publication",
)


def _frame_count(path: Path) -> int:
    value = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    return int(value)


def _audio_sample_count(path: Path) -> int:
    payload = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    assert len(payload) % 4 == 0
    return len(payload) // 4


def _audio_end(path: Path) -> float:
    payload = json.loads(subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
            "-show_entries", "packet=pts_time,duration_time", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout)
    return max(
        float(packet["pts_time"]) + float(packet.get("duration_time", 0.0))
        for packet in payload["packets"]
    )


@pytest.fixture(scope="module")
def fixed_profile(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("phase-profile") / "soft-fixed.yaml"
    path.write_text(
        SOFT_PROFILE.read_text(encoding="utf-8") + "\nseed_strategy: fixed\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def phase_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("phase-source") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "24", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


@needs_ffmpeg
@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="SIGKILL is a POSIX qualification")
@pytest.mark.parametrize("phase", PHASES)
def test_sigkill_and_resume_at_every_media_phase(
    phase: str,
    phase_clip: Path,
    fixed_profile: Path,
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready.json"
    output = tmp_path / "output.mp4"
    work = tmp_path / "work"
    process = subprocess.Popen(
        [
            sys.executable,
            str(FAULT_CHILD),
            "--source", str(phase_clip),
            "--profile", str(fixed_profile),
            "--output", str(output),
            "--work", str(work),
            "--ready", str(ready),
            "--phase", phase,
        ],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 90
    while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not ready.is_file():
        stderr = process.communicate(timeout=5)[1].decode("utf-8", errors="replace")
        pytest.fail(f"phase {phase} was not reached: {stderr[-2000:]}")
    marker = json.loads(ready.read_text(encoding="utf-8"))
    assert marker == {"phase": phase, "pid": process.pid}
    os.killpg(process.pid, signal.SIGKILL)
    assert process.wait(timeout=15) != 0

    subprocess.run(
        [
            sys.executable, "-m", "yt_uniquifier", "run", str(phase_clip),
            "--profile", str(fixed_profile),
            "--out", str(output),
            "--work-dir", str(work),
            "--encoder", "libx264",
            "--segment-sec", "2",
            "--accept-watermark-risk",
            "--no-progress",
            "--no-qa",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    assert _frame_count(output) == _frame_count(phase_clip)
    assert abs(_audio_sample_count(output) - _audio_sample_count(phase_clip)) <= 2 * 1024
    assert abs(_audio_end(output) - _audio_end(phase_clip)) <= 0.05
    states = list(work.glob("*/state.json"))
    assert len(states) == 1
    state = json.loads(states[0].read_text(encoding="utf-8"))
    assert state["output_path"] == str(output)
    assert all(segment["status"] == "done" for segment in state["segments"])
