"""`yt-uniq profile …` — browse and install community profiles.

v0.9.0 R1 / F9 — thin CLI wrapper over ``core.profile_marketplace``.
Mirrors the ``corpus`` subapp shape (typer.Typer + typed Path
arguments + rich tables). No business logic; all validation,
caching, and SHA pinning live in ``core/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_marketplace import (
    DEFAULT_CATALOG_URL,
    MarketplaceError,
    default_install_dir,
    fetch_catalog,
    find_entry,
    install,
    list_entries,
    purge_cache,
)

profile_app = typer.Typer(
    no_args_is_help=True,
    help="Browse and install community-contributed YAML profiles.",
)
console = Console()


@profile_app.command("list-community")
def cmd_list_community(
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Re-fetch the catalog even if a cached copy exists.",
    ),
    url: str = typer.Option(
        DEFAULT_CATALOG_URL,
        "--url",
        help="Override the catalog URL (advanced).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List entries from the marketplace catalog."""
    try:
        catalog = fetch_catalog(url=url, refresh=refresh)
    except MarketplaceError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    entries = list_entries(catalog)
    if json_output:
        typer.echo(
            json.dumps(
                [e.model_dump(mode="json") for e in entries],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not entries:
        console.print("[dim]Catalog is empty.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("name")
    table.add_column("tags")
    table.add_column("version")
    table.add_column("author")
    for e in entries:
        table.add_row(
            e.id,
            e.name,
            ", ".join(e.tags),
            e.version,
            e.author,
        )
    console.print(table)


@profile_app.command("show")
def cmd_show(
    entry_id: str = typer.Argument(..., help="Catalog entry id, e.g. 'cid_aware'."),
    refresh: bool = typer.Option(False, "--refresh"),
    url: str = typer.Option(DEFAULT_CATALOG_URL, "--url"),
) -> None:
    """Print the full catalog entry for inspection before install."""
    try:
        catalog = fetch_catalog(url=url, refresh=refresh)
        entry = find_entry(catalog, entry_id)
    except MarketplaceError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(data=entry.model_dump(mode="json"))


@profile_app.command("install")
def cmd_install(
    entry_id: str = typer.Argument(..., help="Catalog entry id to install."),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        help="Destination directory; defaults to ~/.config/yt_uniquifier/profiles/.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace the file if it already exists.",
    ),
    refresh: bool = typer.Option(False, "--refresh"),
    url: str = typer.Option(DEFAULT_CATALOG_URL, "--url"),
) -> None:
    """Download, verify, and install a community profile."""
    try:
        catalog = fetch_catalog(url=url, refresh=refresh)
        entry = find_entry(catalog, entry_id)
        result = install(entry, dest_dir=dest, overwrite=overwrite)
    except (MarketplaceError, YtUniquifierError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]installed[/green] {result.entry_id} → {result.path} "
        f"(profile name: {result.profile_name})"
    )


@profile_app.command("purge-cache")
def cmd_purge_cache() -> None:
    """Delete the local catalog cache so the next call re-fetches."""
    purge_cache()
    console.print("[green]catalog cache purged[/green]")


@profile_app.command("install-dir")
def cmd_install_dir() -> None:
    """Print the default install directory (useful for shell scripts)."""
    typer.echo(str(default_install_dir()))
