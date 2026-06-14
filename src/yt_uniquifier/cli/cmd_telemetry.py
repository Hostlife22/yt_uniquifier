"""`yt-uniq telemetry …` — inspect, export, and purge local telemetry.

v0.9.0 R3 — telemetry is off by default and local-only. This subapp
exists so the user can see what (if anything) has been recorded,
export it for sharing, or wipe it.

There is no ``enable`` subcommand on purpose: turning telemetry on
requires the user to attach a ``TelemetryConfig`` to their workflow
(GUI Settings or programmatic RunOptions). A CLI flag would make
"enabled" too easy to set unintentionally in a script — telemetry
should be a deliberate, persistent choice.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.core.telemetry import (
    default_consent_marker,
    default_events_dir,
    event_count,
    export_events,
    has_consent_marker,
    purge_events,
)

telemetry_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect, export, or purge local telemetry (off by default).",
)
console = Console()


@telemetry_app.command("status")
def cmd_status(
    events_dir: Path | None = typer.Option(
        None, "--events-dir",
        help="Override the per-user telemetry directory.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show telemetry directory, event count, and consent state."""
    root = events_dir or default_events_dir()
    count = event_count(root)
    consent = has_consent_marker()
    marker_path = default_consent_marker()
    if json_output:
        typer.echo(json.dumps({
            "events_dir": str(root),
            "event_count": count,
            "has_consent_marker": consent,
            "consent_marker_path": str(marker_path),
        }, indent=2))
        return
    console.print(f"events_dir:           {root}")
    console.print(f"event_count:          {count}")
    console.print(f"has_consent_marker:   {consent}")
    console.print(f"consent_marker_path:  {marker_path}")
    if not consent:
        console.print(
            "[dim]No consent decision recorded. Open the GUI Settings → "
            "Telemetry section to make an explicit choice, or stay opted "
            "out (the default).[/dim]"
        )


@telemetry_app.command("export")
def cmd_export(
    dest: Path = typer.Argument(..., help="Destination .jsonl file."),
    events_dir: Path | None = typer.Option(
        None, "--events-dir",
        help="Override the per-user telemetry directory.",
    ),
) -> None:
    """Copy events to a JSONL file (suitable for sharing or archival)."""
    count = export_events(dest, events_dir)
    console.print(f"[green]exported[/green] {count} events → {dest}")


@telemetry_app.command("purge")
def cmd_purge(
    yes: bool = typer.Option(
        False, "--yes",
        help="Required confirmation; without it the command is a no-op.",
    ),
    events_dir: Path | None = typer.Option(
        None, "--events-dir",
        help="Override the per-user telemetry directory.",
    ),
) -> None:
    """Remove every recorded event (irreversible)."""
    if not yes:
        console.print(
            "[yellow]nothing purged[/yellow] — re-run with --yes to confirm."
        )
        raise typer.Exit(code=1)
    purge_events(events_dir)
    console.print("[green]telemetry purged[/green]")
