"""`yt-uniq corpus …` — manage the local fingerprint corpus."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.qa.corpus import Corpus

corpus_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the local fingerprint corpus of previously-known files.",
)
console = Console()


@corpus_app.command("add")
def cmd_add(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    samples: int = typer.Option(60, "--samples", help="Frames sampled for pHash."),
    corpus_dir: Path | None = typer.Option(
        None, "--corpus-dir", help="Override default corpus location."
    ),
) -> None:
    """Index a file's fingerprint into the corpus."""
    try:
        corpus = Corpus(corpus_dir)
        entry = corpus.add(path, samples=samples)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]added[/green] {entry.id}  {entry.path}")
    console.print(
        f"  duration {entry.duration_sec:.1f}s · {entry.sample_count} frames · "
        f"audio_fp {'yes' if entry.audio_fingerprint else 'no'}"
    )


@corpus_app.command("list")
def cmd_list(
    json_output: bool = typer.Option(False, "--json"),
    corpus_dir: Path | None = typer.Option(None, "--corpus-dir"),
) -> None:
    """List all corpus entries."""
    corpus = Corpus(corpus_dir)
    entries = corpus.list_all()
    if json_output:
        typer.echo(json.dumps([
            {
                "id": e.id, "path": str(e.path),
                "added_at": e.added_at,
                "duration_sec": e.duration_sec,
                "sample_count": e.sample_count,
                "has_audio_fp": bool(e.audio_fingerprint),
            }
            for e in entries
        ], indent=2))
        return

    if not entries:
        console.print("[dim]Corpus is empty. Use `yt-uniq corpus add <path>`.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("path")
    table.add_column("duration", justify="right")
    table.add_column("frames", justify="right")
    table.add_column("audio_fp", justify="center")
    table.add_column("added")
    for e in entries:
        table.add_row(
            e.id,
            str(e.path),
            f"{e.duration_sec:.0f}s",
            str(e.sample_count),
            "✓" if e.audio_fingerprint else "—",
            datetime.fromtimestamp(e.added_at).strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@corpus_app.command("remove")
def cmd_remove(
    entry_id: str = typer.Argument(...),
    corpus_dir: Path | None = typer.Option(None, "--corpus-dir"),
) -> None:
    """Remove an entry from the corpus by its id."""
    corpus = Corpus(corpus_dir)
    if corpus.remove(entry_id):
        console.print(f"[green]removed[/green] {entry_id}")
    else:
        console.print(f"[yellow]no such entry:[/yellow] {entry_id}")
        raise typer.Exit(code=1)
