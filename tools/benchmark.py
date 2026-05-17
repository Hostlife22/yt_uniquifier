#!/usr/bin/env python3
"""End-to-end benchmark on a real media file.

Captures wall time, RSS peak, segment count, and per-phase timings into a
single CSV row (appended to --csv). Use to track regressions across changes.

Example:
    python tools/benchmark.py tests/fixtures/720.mp4 \\
      --profile src/yt_uniquifier/profiles/cid_aware.yaml \\
      --out /tmp/bench_out.mp4 \\
      --encoder libx264 --workers 4
"""

from __future__ import annotations

import argparse
import csv
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import RunEvent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--encoder", default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--work-dir", type=Path, default=Path(".bench_work"))
    ap.add_argument("--csv", type=Path, default=Path("benchmark.csv"))
    args = ap.parse_args()

    profile = load_profile(args.profile)
    plan = build_plan(args.input, profile, args.encoder)

    phase_started: dict[str, float] = {}
    phase_totals: dict[str, float] = defaultdict(float)
    segments_seen: set[int] = set()

    def on_event(ev: RunEvent) -> None:
        phase = ev.payload.get("phase") or ev.kind
        now = time.monotonic()
        if ev.kind == "progress":
            if phase not in phase_started:
                phase_started[str(phase)] = now
        else:
            if phase in phase_started:
                phase_totals[str(phase)] += now - phase_started.pop(str(phase))
        seg = ev.payload.get("segment")
        if isinstance(seg, int):
            segments_seen.add(seg)

    start = time.monotonic()
    run_full(
        plan,
        RunOptions(
            work_dir=args.work_dir / plan.plan_hash,
            output=args.out,
            encoder_override=args.encoder,
            keep_segments=False,
            enforce_preflight=False,
            workers=args.workers,
        ),
        on_event=on_event,
    )
    wall = time.monotonic() - start
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    row: dict[str, str | int | float] = {
        "input": str(args.input),
        "out": str(args.out),
        "duration_sec": round(plan.source.duration_sec, 2),
        "size_bytes": plan.source.size_bytes,
        "encoder": plan.encoder.name,
        "workers": args.workers,
        "wall_sec": round(wall, 2),
        "rss_peak_kb": rss_kb,
        "segments_seen": len(segments_seen),
        **{f"phase_{k}_sec": round(v, 2) for k, v in phase_totals.items()},
    }

    write_header = not args.csv.exists() or args.csv.stat().st_size == 0
    with args.csv.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    for k, v in row.items():
        print(f"  {k:20s} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
