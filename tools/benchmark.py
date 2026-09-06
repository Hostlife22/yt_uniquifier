#!/usr/bin/env python3
"""End-to-end benchmark on a real media file.

Captures wall time, RSS peak, segment count, and per-phase timings.
Two output modes:

- CSV row (append, default ``benchmark.csv``) — long-running ledger
  for local tracking across many runs.
- JSON document (``--json out.json``, v1.0.0 R4) — single snapshot
  in a stable shape, designed for the perf-regression CI workflow
  to compare commit-to-commit. See ``tools/perf_compare.py``.

Example:

    python tools/benchmark.py tests/fixtures/720.mp4 \\
      --profile src/yt_uniquifier/profiles/cid_aware.yaml \\
      --out /tmp/bench_out.mp4 \\
      --encoder libx264 --workers 4 \\
      --accept-watermark-risk \\
      --json /tmp/bench.json
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from yt_uniquifier import __version__ as yt_uniq_version
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import RunEvent

REPO_ROOT = Path(__file__).resolve().parent.parent


class _DiskUsageSampler:
    """Sample logical bytes, not allocated blocks or device-wide I/O.

    The output directory must be a dedicated benchmark cell directory.
    """

    def __init__(self, roots: list[Path], interval_sec: float = 1.0) -> None:
        self.roots = list(dict.fromkeys(path.resolve() for path in roots))
        self.interval_sec = interval_sec
        self.peak_bytes = 0
        self.errors = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        total = 0
        seen: set[tuple[int, int]] = set()
        for root in self.roots:
            for directory, _, files in os.walk(root, followlinks=False):
                for name in files:
                    path = Path(directory) / name
                    try:
                        if path.is_symlink():
                            continue
                        stat = path.stat()
                    except FileNotFoundError:
                        continue  # concurrent publication or cleanup
                    except OSError:
                        self.errors += 1
                        continue
                    key = (stat.st_dev, stat.st_ino)
                    if key not in seen:
                        total += stat.st_size
                        seen.add(key)
        self.peak_bytes = max(self.peak_bytes, total)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self._sample()

    def start(self) -> None:
        self._sample()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()


class _ProcessTreeMemorySampler:
    """Sample aggregate parent + live FFmpeg child RSS.

    ``resource.RUSAGE_SELF`` measured only this Python process and macOS reports
    it in bytes rather than Linux's KiB. Prefer psutil's cross-platform process
    tree sum; retain an explicitly-labelled approximation when psutil is absent.
    """

    def __init__(self, interval_sec: float = 0.1, *, pid: int | None = None) -> None:
        self.interval_sec = interval_sec
        self.peak_kb = 0
        self.method = "resource_self_plus_children_max"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._psutil: Any | None = None
        self._process: Any | None = None
        self._external_pid = pid
        try:
            import psutil

            self._psutil = psutil
            try:
                self._process = psutil.Process(pid if pid is not None else os.getpid())
                self.method = "psutil_process_tree_sum_100ms"
            except psutil.Error:
                pass
        except (ImportError, OSError):
            pass
        if pid is not None and self._process is None:
            self.method = "unavailable_external_process"

    def start(self) -> None:
        if self._process is None:
            return
        self._sample()
        self._thread = threading.Thread(
            target=self._loop,
            name="benchmark-memory-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> tuple[int, str]:
        if self._process is not None:
            self._sample()
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=max(1.0, self.interval_sec * 3))
            return self.peak_kb, self.method
        if self._external_pid is not None:
            return 0, self.method
        return self._resource_fallback_kb(), self.method

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self._sample()

    def _sample(self) -> None:
        assert self._process is not None
        assert self._psutil is not None
        rss_bytes = 0
        processes = [self._process]
        with contextlib.suppress(self._psutil.Error, OSError):
            processes.extend(self._process.children(recursive=True))
        for process in processes:
            try:
                rss_bytes += int(process.memory_info().rss)
            except (self._psutil.Error, OSError):
                continue
        self.peak_kb = max(self.peak_kb, rss_bytes // 1024)

    @staticmethod
    def _resource_fallback_kb() -> int:
        try:
            import resource

            self_peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            child_peak = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        except (ImportError, OSError):
            return 0
        total = self_peak + child_peak
        # Darwin documents bytes; Linux and the BSDs used in CI report KiB.
        return total // 1024 if platform.system() == "Darwin" else total


def _git_sha() -> str:
    """Short git SHA for the current HEAD. Returns 'unknown' if git absent."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _implementation_digest() -> str:
    """Identify dirty local implementation, not just the last committed HEAD."""
    digest = hashlib.sha256()
    paths = sorted((REPO_ROOT / "src").rglob("*.py")) + [Path(__file__).resolve()]
    for path in paths:
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--encoder", default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument(
        "--sample-disk", action="store_true",
        help="Sample work/output directory bytes; use a dedicated output directory.",
    )
    ap.add_argument(
        "--accept-watermark-risk",
        action="store_true",
        help="Attest that the benchmark input is owned/licensed content.",
    )
    ap.add_argument("--work-dir", type=Path, default=Path(".bench_work"))
    ap.add_argument("--csv", type=Path, default=Path("benchmark.csv"))
    ap.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also write a structured JSON snapshot to this path. "
        "Shape is stable across releases; consumed by "
        "tools/perf_compare.py.",
    )
    ap.add_argument(
        "--plan-json",
        type=Path,
        default=None,
        help="Write the exact Plan used by this run for registered QA metrics.",
    )
    args = ap.parse_args()
    implementation_digest = _implementation_digest()

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

    memory_sampler = _ProcessTreeMemorySampler()
    disk_sampler = _DiskUsageSampler([args.work_dir, args.out.parent]) if args.sample_disk else None
    if disk_sampler is not None:
        disk_sampler.start()
    memory_sampler.start()
    start = time.monotonic()
    try:
        run_full(
            plan,
            RunOptions(
                work_dir=args.work_dir / plan.plan_hash,
                output=args.out,
                encoder_override=args.encoder,
                keep_segments=False,
                enforce_preflight=True,
                workers=args.workers,
                accept_watermark_risk=args.accept_watermark_risk,
            ),
            on_event=on_event,
        )
    finally:
        rss_kb, rss_method = memory_sampler.stop()
        if disk_sampler is not None:
            disk_sampler.stop()
    wall = time.monotonic() - start

    row: dict[str, str | int | float] = {
        "input": str(args.input),
        "out": str(args.out),
        "duration_sec": round(plan.source.duration_sec, 2),
        "size_bytes": plan.source.size_bytes,
        "output_size_bytes": args.out.stat().st_size,
        "size_ratio": round(args.out.stat().st_size / max(plan.source.size_bytes, 1), 6),
        "encoder": plan.encoder.name,
        "workers": args.workers,
        "wall_sec": round(wall, 2),
        "rss_peak_kb": rss_kb,
        "rss_method": rss_method,
        "segments_seen": len(segments_seen),
        **{f"phase_{k}_sec": round(v, 2) for k, v in phase_totals.items()},
    }

    write_header = not args.csv.exists() or args.csv.stat().st_size == 0
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    # v1.0.0 R4 — emit the same data as a structured JSON snapshot for
    # the perf-regression CI workflow. Keys are intentionally a strict
    # superset of the CSV fields plus environment metadata. Future
    # benchmark.py changes must keep this shape additive — see
    # tools/perf_compare.py for the consumer contract.
    if args.json is not None:
        snapshot = {
            "schema_version": 1,
            "yt_uniquifier_version": yt_uniq_version,
            "git_sha": _git_sha(),
            "implementation_sha256_at_start": implementation_digest,
            "py_version": platform.python_version(),
            "platform": f"{platform.system()}-{platform.machine()}",
            "input": str(args.input),
            "out": str(args.out),
            "duration_sec": round(plan.source.duration_sec, 2),
            "size_bytes": plan.source.size_bytes,
            "output_size_bytes": args.out.stat().st_size,
            "size_ratio": round(
                args.out.stat().st_size / max(plan.source.size_bytes, 1), 6,
            ),
            "encoder": plan.encoder.name,
            "workers": args.workers,
            "wall_sec": round(wall, 2),
            "rss_peak_kb": rss_kb,
            "rss_method": rss_method,
            "segments_seen": len(segments_seen),
            "per_phase_sec": {k: round(v, 2) for k, v in phase_totals.items()},
            "disk_peak_logical_bytes": disk_sampler.peak_bytes if disk_sampler else None,
            "disk_measurement": {
                "method": "deduplicated_logical_file_sizes_1s_sampled_lower_bound",
                "enabled": disk_sampler is not None,
                "read_errors": disk_sampler.errors if disk_sampler else None,
                "scope": "work and dedicated output directories; not device I/O",
            },
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.plan_json is not None:
        args.plan_json.parent.mkdir(parents=True, exist_ok=True)
        args.plan_json.write_text(
            plan.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    for k, v in row.items():
        print(f"  {k:20s} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
