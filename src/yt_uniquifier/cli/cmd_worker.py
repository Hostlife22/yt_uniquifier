"""`yt-uniq worker <queue_dir>` — long-running queue drainer."""

from __future__ import annotations

import contextlib
import signal
import threading
import traceback
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.queue.leasing import FileQueue
from yt_uniquifier.core.runner import CancelToken

console = Console()


def worker_cmd(
    queue_dir: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True,
        help="Shared queue directory (init with `yt-uniq queue init`).",
    ),
    profile: Path = typer.Option(..., "--profile", help="YAML profile."),
    out_dir: Path = typer.Option(..., "--out-dir", help="Destination directory."),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    workers: int = typer.Option(
        1, "--workers",
        help="Parallel segment workers within this worker process.",
    ),
    work_dir: Path = typer.Option(
        Path(".yt_uniq_work"), "--work-dir",
        help="Per-worker scratch area (segments / state.json).",
    ),
    stop_after_empty: bool = typer.Option(
        False, "--stop-after-empty",
        help="Exit cleanly when the queue has no pending files.",
    ),
    heartbeat_sec: float = typer.Option(30.0, "--heartbeat-sec"),
    poll_sec: float = typer.Option(5.0, "--poll-sec"),
    reap_every_n: int = typer.Option(
        4, "--reap-every-n",
        help="Run reap_stale after this many lease attempts.",
    ),
    keep_segments: bool = typer.Option(False, "--keep-segments"),
) -> None:
    """Drain a queue: lease → run_full → release_done/failed → repeat."""
    q = FileQueue(queue_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        prof = load_profile(profile)
    except YtUniquifierError as exc:
        console.print(f"[red]profile error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    stop = threading.Event()
    cancel = CancelToken()

    # SIGINT / SIGTERM → cooperative cancel: set the worker-level stop flag
    # (so the lease/poll loop exits at its next iteration) AND the
    # CancelToken passed into run_full (so the in-flight file's segment
    # encodes terminate at their next ffmpeg-progress tick instead of
    # being SIGKILL'd mid-encode and leaving half-written segments).
    def _on_signal(_signum: int, _frame: Any) -> None:
        cancel.cancel()
        stop.set()

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)

    hb_thread = threading.Thread(
        target=_heartbeat_loop, args=(q, stop, heartbeat_sec), daemon=True,
    )
    hb_thread.start()

    poll_count = 0
    processed = 0
    failed = 0
    try:
        while not stop.is_set():
            poll_count += 1
            if poll_count % max(1, reap_every_n) == 0:
                reaped = q.reap_stale()
                if reaped:
                    console.print(f"[dim]reaped {reaped} stale leases[/dim]")

            leased = q.lease()
            if leased is None:
                if stop_after_empty:
                    break
                stop.wait(poll_sec)
                continue

            console.print(f"[bold]→ {leased.name}[/bold]")
            suffix = f".{prof.output_container}"
            output = out_dir / f"{leased.stem}.uniq{suffix}"
            staged = q.staged_output_path(output)
            try:
                _process_one(leased, prof, out_dir, encoder_override,
                              work_dir, workers, keep_segments,
                              cancel_token=cancel, output=staged)
                q.commit_output(leased, staged, output)
                processed += 1
                console.print(f"[green]✓[/green] {leased.name}")
            except Exception as exc:  # noqa: BLE001 — worker swallows per-file
                staged.unlink(missing_ok=True)
                tb = "".join(traceback.format_exception(type(exc), exc,
                                                         exc.__traceback__))
                if leased.exists():
                    q.release_failed(leased, tb)
                else:
                    console.print(
                        f"[yellow]lease lost before publish:[/yellow] {leased.name}"
                    )
                failed += 1
                console.print(f"[red]✗[/red] {leased.name} — {exc}")
    finally:
        stop.set()
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        hb_thread.join(timeout=2)
        console.print(f"\nsummary: {processed} ok, {failed} failed")


def _heartbeat_loop(q: FileQueue, stop: threading.Event,
                     interval_sec: float) -> None:
    while not stop.is_set():
        with contextlib.suppress(Exception):
            q.heartbeat()
        stop.wait(interval_sec)


def _process_one(
    leased: Path, profile: Profile, out_dir: Path,
    encoder_override: str | None, work_root: Path,
    workers: int, keep_segments: bool,
    *,
    cancel_token: CancelToken | None = None,
    output: Path | None = None,
) -> None:
    out = output or out_dir / f"{leased.stem}.uniq.{profile.output_container}"
    plan = build_plan(leased, profile, encoder_override)
    options = RunOptions(
        work_dir=work_root / plan.plan_hash,
        output=out,
        encoder_override=encoder_override,
        keep_segments=keep_segments,
        enforce_preflight=True,
        workers=workers,
    )
    run_full(plan, options, cancel_token=cancel_token)
