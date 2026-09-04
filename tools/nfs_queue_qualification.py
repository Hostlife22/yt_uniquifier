"""Deterministic NFS queue qualification primitives used by the Docker lab.

The tool intentionally operates on placeholder bytes, never user media.  It tests
the shared-filesystem coordination contract around the existing ``FileQueue`` API:
exactly-once leasing, stale-owner reaping, stale commit fencing, and durable commit
journal recovery.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from yt_uniquifier.core.queue.leasing import FileQueue, QueueError, init_queue


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _init(args: argparse.Namespace) -> int:
    init_queue(args.root)
    return 0


def _seed(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    for index in range(args.count):
        source = args.root / f"seed-{args.prefix}-{index:04d}.bin"
        source.write_bytes(f"{args.prefix}:{index}\n".encode())
        try:
            queue.add(source)
        finally:
            source.unlink(missing_ok=True)
    return 0


def _drain(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    leased_names: list[str] = []
    while leased := queue.lease():
        leased_names.append(leased.name)
        queue.release_done(leased)
    _write_json(
        args.result,
        {"worker": args.worker, "leased": leased_names, "count": len(leased_names)},
    )
    return 0


def _verify(args: argparse.Namespace) -> int:
    result_files = sorted(args.results.glob("drain-*.json"))
    batches = [json.loads(path.read_text(encoding="utf-8")) for path in result_files]
    names = [name for batch in batches for name in batch["leased"]]
    queue = FileQueue(args.root, host="verifier")
    stats = queue.stats()
    valid = (
        len(result_files) == args.workers
        and len(names) == args.expected
        and len(set(names)) == args.expected
        and stats == {
            "pending": 0,
            "in_progress": 0,
            "done": args.expected,
            "failed": 0,
        }
    )
    payload: dict[str, object] = {
        "scenario": "concurrent_lease",
        "valid": valid,
        "workers": args.workers,
        "expected": args.expected,
        "unique": len(set(names)),
        "stats": stats,
    }
    _write_json(args.result, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if valid else 1


def _partition_worker(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    leased = queue.lease()
    if leased is None:
        raise RuntimeError("partition scenario has no pending input")
    queue.heartbeat()
    output = args.output / "partition-output.bin"
    args.output.mkdir(parents=True, exist_ok=True)
    staged = queue.staged_output_path(output)
    staged.write_bytes(b"stale worker output must not publish\n")
    _write_json(args.ready, {"worker": args.worker, "leased": leased.name})
    deadline = time.monotonic() + args.resume_timeout
    while not args.resume.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"resume marker was not created: {args.resume}")
        time.sleep(0.1)
    rejected = False
    try:
        queue.commit_output(leased, staged, output)
    except QueueError:
        rejected = True
        staged.unlink(missing_ok=True)
    payload: dict[str, object] = {
        "scenario": "partition_fencing",
        "stale_commit_rejected": rejected,
        "output_published": output.exists(),
    }
    _write_json(args.result, payload)
    return 0 if rejected and not output.exists() else 1


def _reap(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    count = queue.reap_stale(stale_sec=args.stale_sec)
    payload: dict[str, object] = {"scenario": "stale_reap", "reaped": count}
    _write_json(args.result, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if count == args.expected else 1


def _crash_after_fence(args: argparse.Namespace) -> int:
    from yt_uniquifier.core.queue import leasing as leasing_mod

    queue = FileQueue(args.root, host=args.worker)
    leased = queue.lease()
    if leased is None:
        return 70
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / "recovered-output.bin"
    staged = queue.staged_output_path(output)
    staged.write_bytes(b"durable fenced output\n")
    real_replace = leasing_mod.os.replace

    def crash_on_publish(source: Path, destination: Path) -> None:
        if Path(source) == staged and Path(destination) == output:
            os._exit(73)
        real_replace(source, destination)

    leasing_mod.os.replace = crash_on_publish
    queue.commit_output(leased, staged, output)
    return 71


def _recover(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    recovered = queue.recover_commits(args.output, stale_sec=0)
    output = args.output / "recovered-output.bin"
    valid = recovered == 1 and output.read_bytes() == b"durable fenced output\n"
    payload: dict[str, object] = {
        "scenario": "crash_journal_recovery",
        "valid": valid,
        "recovered": recovered,
        "stats": queue.stats(),
    }
    _write_json(args.result, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if valid else 1


def _path(value: str) -> Path:
    return Path(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("root", type=_path)
    init.set_defaults(handler=_init)

    seed = commands.add_parser("seed")
    seed.add_argument("root", type=_path)
    seed.add_argument("--count", type=int, required=True)
    seed.add_argument("--prefix", default="job")
    seed.add_argument("--worker", default="producer")
    seed.set_defaults(handler=_seed)

    drain = commands.add_parser("drain")
    drain.add_argument("root", type=_path)
    drain.add_argument("--worker", required=True)
    drain.add_argument("--result", type=_path, required=True)
    drain.set_defaults(handler=_drain)

    verify = commands.add_parser("verify")
    verify.add_argument("root", type=_path)
    verify.add_argument("--results", type=_path, required=True)
    verify.add_argument("--result", type=_path, required=True)
    verify.add_argument("--expected", type=int, required=True)
    verify.add_argument("--workers", type=int, required=True)
    verify.set_defaults(handler=_verify)

    partition = commands.add_parser("partition-worker")
    partition.add_argument("root", type=_path)
    partition.add_argument("--output", type=_path, required=True)
    partition.add_argument("--worker", default="partitioned-worker")
    partition.add_argument("--resume", type=_path, required=True)
    partition.add_argument("--resume-timeout", type=float, default=30.0)
    partition.add_argument("--ready", type=_path, required=True)
    partition.add_argument("--result", type=_path, required=True)
    partition.set_defaults(handler=_partition_worker)

    reap = commands.add_parser("reap")
    reap.add_argument("root", type=_path)
    reap.add_argument("--worker", default="reaper")
    reap.add_argument("--stale-sec", type=int, default=1)
    reap.add_argument("--expected", type=int, default=1)
    reap.add_argument("--result", type=_path, required=True)
    reap.set_defaults(handler=_reap)

    crash = commands.add_parser("crash-after-fence")
    crash.add_argument("root", type=_path)
    crash.add_argument("--output", type=_path, required=True)
    crash.add_argument("--worker", default="crash-worker")
    crash.set_defaults(handler=_crash_after_fence)

    recover = commands.add_parser("recover")
    recover.add_argument("root", type=_path)
    recover.add_argument("--output", type=_path, required=True)
    recover.add_argument("--worker", default="recovery-worker")
    recover.add_argument("--result", type=_path, required=True)
    recover.set_defaults(handler=_recover)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
