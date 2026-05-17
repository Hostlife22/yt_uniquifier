"""`yt-uniq queue …` — manage a shared-FS distributed work queue."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.queue.leasing import FileQueue, init_queue

queue_app = typer.Typer(
    no_args_is_help=True,
    help="Manage a shared-FS distributed work queue (NFSv4/ZFS/ext4).",
)
console = Console()


@queue_app.command("init")
def cmd_init(
    queue_dir: Path = typer.Argument(..., help="Shared directory to host the queue."),
) -> None:
    """Create pending/in_progress/done/failed and verify atomic rename works."""
    try:
        layout = init_queue(queue_dir)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]ok[/green] {layout.root}")
    for d in ("pending", "in_progress", "done", "failed"):
        console.print(f"  {d}/")


@queue_app.command("add")
def cmd_add(
    queue_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    paths: list[Path] = typer.Argument(  # noqa: B008
        ..., help="Files to enqueue. Hard-linked if same FS, else copied.",
    ),
) -> None:
    """Enqueue one or more files."""
    q = FileQueue(queue_dir)
    for p in paths:
        try:
            dest = q.add(p)
        except FileExistsError as exc:
            console.print(f"[yellow]skip[/yellow] already queued: {exc}")
            continue
        except FileNotFoundError:
            console.print(f"[red]missing[/red] {p}")
            continue
        console.print(f"[green]queued[/green] {dest.name}")


@queue_app.command("status")
def cmd_status(
    queue_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show counts per bucket."""
    q = FileQueue(queue_dir)
    s = q.stats()
    if json_output:
        typer.echo(json.dumps(s, indent=2))
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("state")
    table.add_column("count", justify="right")
    for k in ("pending", "in_progress", "done", "failed"):
        table.add_row(k, str(s[k]))
    console.print(table)


@queue_app.command("reset")
def cmd_reset(
    queue_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    stale_sec: int = typer.Option(
        300, "--stale-sec",
        help="Heartbeat staleness threshold; in_progress files older than this "
             "are moved back to pending.",
    ),
) -> None:
    """Re-queue stale in_progress entries whose host hasn't heartbeated."""
    q = FileQueue(queue_dir)
    n = q.reap_stale(stale_sec=stale_sec)
    console.print(f"reaped {n} stale leases")
