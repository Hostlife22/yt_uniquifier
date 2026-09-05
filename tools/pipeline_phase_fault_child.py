"""Child process used by the POSIX full-pipeline SIGKILL qualification.

This is deliberately separate from production orchestration: no fault-injection
switch is exposed by the application itself. The parent test kills this process
after the requested durable boundary announces itself through ``ready``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import RunEvent


def _wait_for_sigkill(ready: Path, phase: str) -> None:
    ready.write_text(
        json.dumps({"phase": phase, "pid": os.getpid()}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    while True:
        time.sleep(60)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=(
            "after_probe",
            "after_plan",
            "after_segment",
            "during_audio",
            "during_concat",
            "during_validation",
            "after_publication",
        ),
        required=True,
    )
    args = parser.parse_args()
    profile = load_profile(args.profile)
    plan = build_plan(args.source, profile, "libx264")
    if args.phase == "after_probe":
        _wait_for_sigkill(args.ready, args.phase)

    triggered = False

    def on_event(event: RunEvent) -> None:
        nonlocal triggered
        if triggered:
            return
        event_phase = event.payload.get("phase")
        matches = (
            (args.phase == "after_plan" and event_phase == "plan")
            or (
                args.phase == "after_segment"
                and event_phase == "segment"
                and event.payload.get("status") == "done"
            )
            or (args.phase == "during_audio" and event_phase == "main_audio")
            or (args.phase == "during_concat" and event_phase == "concat")
            or (args.phase == "during_validation" and event_phase == "validation")
            or (
                args.phase == "after_publication"
                and event_phase == "disk"
                and event.payload.get("target") == "final output"
                and event.payload.get("reserved_bytes") == 0
            )
        )
        if matches:
            triggered = True
            _wait_for_sigkill(args.ready, args.phase)

    run_full(
        plan,
        RunOptions(
            # Match the public CLI's per-plan checkpoint namespace so the
            # parent resumes the exact same durable state.
            work_dir=args.work / plan.plan_hash,
            output=args.output,
            encoder_override="libx264",
            target_segment_sec=2,
            keep_segments=True,
            accept_watermark_risk=True,
            run_id=f"phase-fault-{args.phase}",
        ),
        on_event=on_event,
    )
    raise RuntimeError(f"fault boundary was not observed: {args.phase}")


if __name__ == "__main__":
    raise SystemExit(main())
