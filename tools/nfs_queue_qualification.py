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
import signal
import time
from pathlib import Path

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.errors import CheckpointError
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
    Segment,
    SourceMeta,
)
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


def _wait_for_sigkill(ready: Path, *, phase: str, worker: str) -> None:
    _write_json(
        ready,
        {"phase": phase, "worker": worker, "pid": os.getpid(), "ready": True},
    )
    while True:
        signal.pause()


def _crash_commit_phase(args: argparse.Namespace) -> int:
    """Pause at one commit boundary so the Docker harness can SIGKILL us."""
    from yt_uniquifier.core.queue import leasing as leasing_mod

    queue = FileQueue(args.root, host=args.worker)
    leased = queue.lease()
    if leased is None:
        return 70
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"{args.phase}-output.bin"
    staged = queue.staged_output_path(output)
    staged.write_bytes(f"original:{args.phase}\n".encode())

    if args.phase == "after-stage":
        _wait_for_sigkill(args.ready, phase=args.phase, worker=args.worker)

    if args.phase == "after-journal":
        real_write_journal = queue._write_commit_journal

        def pause_after_journal(
            leased_path: Path, staged_path: Path, output_path: Path,
        ) -> tuple[Path, Path]:
            journal_and_fence = real_write_journal(
                leased_path, staged_path, output_path,
            )
            _wait_for_sigkill(args.ready, phase=args.phase, worker=args.worker)
            return journal_and_fence

        queue._write_commit_journal = pause_after_journal  # type: ignore[method-assign]
    elif args.phase in {"after-fence", "after-publish"}:
        real_replace = leasing_mod.os.replace

        def pause_on_replace(source: Path, destination: Path) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            should_pause = (
                args.phase == "after-fence"
                and source_path == staged
                and destination_path == output
            ) or (
                args.phase == "after-publish"
                and source_path.name.startswith(".commit-")
                and source_path.name.endswith(".fence")
                and destination_path.parent == queue.layout.done
            )
            if should_pause:
                _wait_for_sigkill(args.ready, phase=args.phase, worker=args.worker)
            real_replace(source, destination)

        leasing_mod.os.replace = pause_on_replace

    queue.commit_output(leased, staged, output)
    return 71


def _recover_commit_phase(args: argparse.Namespace) -> int:
    queue = FileQueue(args.root, host=args.worker)
    reaped = queue.reap_stale(stale_sec=0)
    recovered = queue.recover_commits(args.output, stale_sec=0)
    output = args.output / f"{args.phase}-output.bin"
    expected_original = f"original:{args.phase}\n".encode()
    retried = False
    if args.phase in {"after-stage", "after-journal"}:
        leased = queue.lease()
        if leased is None:
            raise RuntimeError(f"{args.phase}: killed lease was not reaped")
        staged = queue.staged_output_path(output)
        staged.write_bytes(f"retried:{args.phase}\n".encode())
        queue.commit_output(leased, staged, output)
        retried = True
        expected = f"retried:{args.phase}\n".encode()
    else:
        expected = expected_original

    first_payload = output.read_bytes() if output.is_file() else b""
    recovered_again = queue.recover_commits(args.output, stale_sec=0)
    second_payload = output.read_bytes() if output.is_file() else b""
    stale_parts = sorted(path.name for path in args.output.glob(".*.part.bin"))
    for stale in stale_parts:
        (args.output / stale).unlink(missing_ok=True)
    valid = (
        first_payload == expected
        and second_payload == expected
        and recovered_again == 0
        and queue.stats()["done"] == 1
    )
    payload: dict[str, object] = {
        "scenario": "sigkill_commit_phase",
        "phase": args.phase,
        "valid": valid,
        "reaped": reaped,
        "recovered": recovered,
        "recovered_again": recovered_again,
        "retried": retried,
        "stale_parts_removed": stale_parts,
        "stats": queue.stats(),
    }
    _write_json(args.result, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if valid else 1


def _checkpoint_plan(root: Path) -> Plan:
    source_path = root / "checkpoint-source.bin"
    source_path.write_bytes(b"licensed-placeholder\n")
    return Plan(
        source=SourceMeta(
            path=source_path,
            container="mkv",
            duration_sec=1.0,
            size_bytes=source_path.stat().st_size,
        ),
        profile=Profile(name="nfs-checkpoint", transforms=[]),
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="nfs-checkpoint-contract",
    )


def _corrupt_checkpoint(args: argparse.Namespace) -> int:
    work = args.root / "corrupt-checkpoint"
    work.mkdir(parents=True, exist_ok=True)
    (work / "state.json").write_text("{not valid json", encoding="utf-8")
    store = CheckpointStore(work, _checkpoint_plan(args.root))
    rejected = False
    try:
        store.init_or_resume([Segment(idx=0, start_sec=0.0, end_sec=1.0)])
    except CheckpointError:
        rejected = True
    finally:
        store.close()
    payload: dict[str, object] = {
        "scenario": "corrupt_checkpoint",
        "valid": rejected,
        "corruption_rejected": rejected,
    }
    _write_json(args.result, payload)
    return 0 if rejected else 1


def _disk_full_checkpoint(args: argparse.Namespace) -> int:
    """Fill a bounded tmpfs and prove atomic checkpoint bytes survive ENOSPC."""
    work = args.root / "disk-full-checkpoint"
    store = CheckpointStore(work, _checkpoint_plan(args.root))
    store.init_or_resume([Segment(idx=0, start_sec=0.0, end_sec=1.0)])
    before = store.state_path.read_bytes()
    # Inflate only the next in-memory snapshot. The durable state remains the
    # small known-good file until the fsync+replace transaction succeeds.
    store._state["fault_injection_padding"] = "x" * (512 * 1024)
    stat = os.statvfs(args.root)
    available = stat.f_bavail * stat.f_frsize
    filler = args.root / "filler.bin"
    reserve = 128 * 1024
    with filler.open("wb") as handle:
        chunk = b"0" * (64 * 1024)
        remaining = max(0, available - reserve)
        while remaining > 0:
            piece = chunk[:min(len(chunk), remaining)]
            try:
                handle.write(piece)
            except OSError:
                break
            remaining -= len(piece)
        try:
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            pass
    failed_with_enospc = False
    try:
        store.flush()
    except OSError:
        failed_with_enospc = True
    after = store.state_path.read_bytes()
    temp_files = list(work.glob("state.json.*.tmp"))
    filler.unlink(missing_ok=True)
    store.close()
    valid = failed_with_enospc and before == after and not temp_files
    payload: dict[str, object] = {
        "scenario": "disk_full_checkpoint",
        "valid": valid,
        "flush_failed": failed_with_enospc,
        "durable_state_preserved": before == after,
        "temporary_files": [path.name for path in temp_files],
    }
    _write_json(args.result, payload)
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

    crash_phase = commands.add_parser("crash-commit-phase")
    crash_phase.add_argument("root", type=_path)
    crash_phase.add_argument("--output", type=_path, required=True)
    crash_phase.add_argument(
        "--phase",
        choices=("after-stage", "after-journal", "after-fence", "after-publish"),
        required=True,
    )
    crash_phase.add_argument("--worker", required=True)
    crash_phase.add_argument("--ready", type=_path, required=True)
    crash_phase.set_defaults(handler=_crash_commit_phase)

    recover_phase = commands.add_parser("recover-commit-phase")
    recover_phase.add_argument("root", type=_path)
    recover_phase.add_argument("--output", type=_path, required=True)
    recover_phase.add_argument(
        "--phase",
        choices=("after-stage", "after-journal", "after-fence", "after-publish"),
        required=True,
    )
    recover_phase.add_argument("--worker", required=True)
    recover_phase.add_argument("--result", type=_path, required=True)
    recover_phase.set_defaults(handler=_recover_commit_phase)

    corrupt = commands.add_parser("corrupt-checkpoint")
    corrupt.add_argument("root", type=_path)
    corrupt.add_argument("--result", type=_path, required=True)
    corrupt.set_defaults(handler=_corrupt_checkpoint)

    disk_full = commands.add_parser("disk-full-checkpoint")
    disk_full.add_argument("root", type=_path)
    disk_full.add_argument("--result", type=_path, required=True)
    disk_full.set_defaults(handler=_disk_full_checkpoint)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
