"""Prepare lossless A/B excerpts and numerical diagnostics, never a listening verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.transforms.audio_loudnorm import measure
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin


def pcm_diagnostics(path: Path, *, channels: int) -> dict[str, Any]:
    """Inspect a bounded review excerpt at its original channel count.

    Pairwise zero-lag Pearson correlation is a diagnostic, not a phase/quality
    score: legitimate surround decorrelation or polarity is not an automatic bug.
    """
    decoded = subprocess.run([
        ffmpeg_bin(), "-v", "error", "-xerror", "-i", str(path),
        "-map", "0:a:0", "-t", "30", "-c:a", "pcm_f32le", "-f", "f32le", "-",
    ], capture_output=True, check=True, timeout=120)
    data = np.frombuffer(decoded.stdout, dtype="<f4")
    if not data.size or data.size % channels:
        raise ValueError("invalid decoded PCM shape")
    data = data.reshape(-1, channels)
    finite = np.isfinite(data)
    bad = int(np.count_nonzero(~finite))
    pairs: list[dict[str, Any]] = []
    for left in range(channels):
        for right in range(left + 1, channels):
            valid = finite[:, left] & finite[:, right]
            left_data, right_data = data[valid, left], data[valid, right]
            correlation = None
            if left_data.size > 1 and np.std(left_data) > 1e-8 and np.std(right_data) > 1e-8:
                correlation = float(np.corrcoef(left_data, right_data)[0, 1])
            pairs.append({"channels": [left, right], "pearson_zero_lag": correlation})
    peaks = [
        float(np.max(np.abs(data[finite[:, index], index])))
        if np.any(finite[:, index]) else None
        for index in range(channels)
    ]
    return {
        "decoded_samples_per_channel": len(data), "channels": channels,
        "nonfinite_values": bad,
        "samples_at_or_above_full_scale_per_channel": [
            int(np.count_nonzero(finite[:, index] & (np.abs(data[:, index]) >= 1)))
            for index in range(channels)
        ],
        "sample_peak_dbfs_per_channel": [
            20 * math.log10(peak) if peak is not None and peak > 0 else None for peak in peaks
        ],
        "channel_correlations": pairs,
        "note": "Silence has no finite dBFS/correlation; correlation is not a listening verdict.",
    }


def prepare_review(
    inputs: dict[str, Path], destination: Path, *, start: float, duration: float,
    rights_reference: str,
) -> dict[str, Any]:
    if not rights_reference.strip():
        raise ValueError("rights_reference is required")
    if not math.isfinite(start) or start < 0 or not 0 < duration <= 30:
        raise ValueError("start must be nonnegative and duration must be in (0, 30]")
    destination.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "rights_reference": rights_reference, "start_sec": start, "duration_sec": duration,
        "listening": "NOT VERIFIED: human review required",
        "gain_policy": "original gain, no normalization for A/B excerpts",
        "clips": [],
    }
    for label, source in inputs.items():
        if not label or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in label):
            raise ValueError("unsafe review label")
        meta = probe(source)
        if start + duration > meta.duration_sec + 0.001:
            raise ValueError(f"{label}: excerpt extends beyond source duration")
        for index, stream in enumerate(meta.audio):
            excerpt = destination / f"{label}-audio-{index}.wav"
            # Float WAV retains overshoots: integer PCM would clip them before measurement.
            subprocess.run([
                ffmpeg_bin(), "-v", "error", "-xerror", "-n", "-i", str(source),
                "-ss", str(start), "-map", f"0:a:{index}", "-t", str(duration),
                "-c:a", "pcm_f32le", str(excerpt),
            ], check=True, capture_output=True, timeout=120)
            loudness = measure(excerpt)
            lufs = loudness.input_i if math.isfinite(loudness.input_i) else None
            true_peak = loudness.input_tp if math.isfinite(loudness.input_tp) else None
            report["clips"].append({
                "variant": label, "stream_index": stream.index,
                "channel_layout": stream.channel_layout,
                "file": excerpt.name, "sha256": hashlib.sha256(excerpt.read_bytes()).hexdigest(),
                "lufs_i": lufs, "true_peak_dbtp": true_peak,
                "loudness_note": (
                    "silent/nonfinite measurement is unavailable"
                    if lufs is None or true_peak is None else None
                ),
                "scope": "excerpt only; boundary transients are not full-file peak evidence",
                "pcm": pcm_diagnostics(excerpt, channels=stream.channels),
            })
    (destination / "review.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--proposed", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--rights-reference", required=True)
    args = parser.parse_args()
    inputs = {"source": args.source, "current": args.current}
    if args.proposed:
        inputs["proposed"] = args.proposed
    prepare_review(
        inputs, args.out, start=args.start, duration=args.duration,
        rights_reference=args.rights_reference,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
