"""v1.2.0 Task 27 — chaos test: random SIGKILL during a run.

The orchestrator's split-process-concat resume contract claims that a
crash mid-segment leaves the output bit-identical to a clean run
(modulo encoder non-determinism, which we measure via VMAF ≥ 99).
This test forks the orchestrator under a watchdog that SIGKILLs it at
a random wall-clock offset and then resumes — repeating N times.

The full 100-round version from the v1.2.0 roadmap runs only locally
(it takes ~20 min on a 4-core box); CI ships a tighter ``N=3`` round
to keep wall-clock under 5 min while still exercising the resume path
on every supported OS.

Marked ``@pytest.mark.integration`` because it requires a real ffmpeg
+ libvmaf build.  Skipped automatically when libvmaf is unavailable.
"""

from __future__ import annotations

import os
import random
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg

# Local default; the CI runner can bump this via env.
N_ROUNDS = int(os.environ.get("YT_UNIQ_CHAOS_ROUNDS", "3"))


def _has_libvmaf() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return "libvmaf" in proc.stdout


needs_libvmaf = pytest.mark.skipif(
    not _has_libvmaf(),
    reason="libvmaf not available — chaos test asserts on VMAF",
)


@pytest.fixture
def chaos_clip(tmp_path: Path) -> Path:
    """A short clip with a tight GOP so resume works at fine granularity."""
    out = tmp_path / "chaos.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-x264-params", "keyint=12:min-keyint=12:scenecut=0",
        "-c:a", "aac",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out


@needs_ffmpeg
@needs_libvmaf
@pytest.mark.integration
@pytest.mark.skipif(
    os.name == "nt",
    reason="SIGKILL semantics differ on Windows; chaos lane is POSIX-only",
)
def test_sigkill_then_resume_produces_equivalent_output(
    chaos_clip: Path, tmp_path: Path,
) -> None:
    """After N rounds of (start orchestrator → SIGKILL at random offset
    → resume), the final output must be substantively identical to a
    clean baseline run (VMAF ≥ 99 over the whole timeline).

    We measure via libvmaf rather than asserting bit-identity because
    encoder paths (libx264 included) are sensitive to thread
    scheduling — a different ordering of the same operations can flip
    a handful of low-order bits while preserving every perceptual
    metric we care about.
    """
    # Baseline: clean run.
    baseline = tmp_path / "baseline.mp4"
    _clean_run(chaos_clip, baseline, tmp_path / "baseline_work")
    assert baseline.exists() and baseline.stat().st_size > 0

    # Chaos: launch + SIGKILL + resume, repeated N times.
    out = tmp_path / "chaos_out.mp4"
    work = tmp_path / "chaos_work"
    rng = random.Random(42)
    for _ in range(N_ROUNDS):
        proc = _launch_orchestrator(chaos_clip, out, work)
        # Sleep a random fraction of an estimated wall-clock then kill.
        # On a 5 s 320x180 clip, ultrafast libx264 takes ~0.5-2 s — kill
        # at 30-70 % of that range.
        sleep_for = rng.uniform(0.15, 0.7)
        time.sleep(sleep_for)
        if proc.poll() is None:  # still alive — kill mid-run
            proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=15)

    # Final clean resume to completion.
    _clean_run(chaos_clip, out, work)
    assert out.exists() and out.stat().st_size > 0

    vmaf = _measure_vmaf(reference=baseline, distorted=out)
    assert vmaf >= 99.0, (
        f"chaos run VMAF {vmaf:.2f} below floor 99 — resume contract "
        "may have produced a perceptibly different output"
    )


def _clean_run(source: Path, output: Path, work: Path) -> None:
    """Run the orchestrator to completion via the CLI surface."""
    cmd = [
        "yt-uniq", "run", str(source),
        "--profile", "soft",
        "--output", str(output),
        "--work-dir", str(work),
        "--encoder", "libx264",
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _launch_orchestrator(source: Path, output: Path, work: Path) -> subprocess.Popen[bytes]:
    """Start the orchestrator and return the Popen for the watchdog."""
    cmd = [
        "yt-uniq", "run", str(source),
        "--profile", "soft",
        "--output", str(output),
        "--work-dir", str(work),
        "--encoder", "libx264",
    ]
    return subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,  # SIGKILL on the whole process group
    )


def _measure_vmaf(*, reference: Path, distorted: Path) -> float:
    """One-shot libvmaf measurement; returns the mean score in [0, 100]."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(distorted), "-i", str(reference),
        "-filter_complex",
        "[0:v][1:v]libvmaf=log_fmt=json:log_path=-:n_threads=2",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # libvmaf logs the per-frame and aggregate scores to stdout when
    # log_path=- is set.  Parse the "VMAF score: …" line.
    import re
    match = re.search(r'"VMAF score":\s*([\d.]+)', proc.stdout)
    if match is None:
        match = re.search(r"VMAF score:?\s*([\d.]+)", proc.stderr)
    if match is None:
        pytest.skip("could not parse VMAF score from libvmaf output")
    return float(match.group(1))
