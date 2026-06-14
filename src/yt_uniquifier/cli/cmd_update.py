"""v1.3.0 Task 33 — ``yt-uniq update`` CLI surface."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console

from yt_uniquifier.core import updater

console = Console()


def update_cmd(
    check_only: Annotated[bool, typer.Option(
        "--check",
        help="Only report whether a newer release exists; do not download.",
    )] = False,
    manifest_url: Annotated[str, typer.Option(
        "--manifest-url",
        help="Override the manifest URL (forks / private mirrors).",
    )] = updater.DEFAULT_MANIFEST_URL,
) -> None:
    """Check for and apply yt-uniquifier updates.

    The remote manifest is fetched over HTTPS, every release asset is
    SHA-pinned, and the asset's cosign bundle is verified against
    GitHub's OIDC identity before the file lands on disk.  Set
    ``YT_UNIQ_DISABLE_UPDATER=1`` in air-gapped environments.
    """
    result = updater.check_for_update(manifest_url=manifest_url)
    if not result.available:
        console.print(
            f"[green]already up to date[/green] "
            f"(installed {result.current_version!r})"
        )
        return
    console.print(
        f"[yellow]update available:[/yellow] "
        f"{result.current_version!r} → {result.latest_version!r}"
    )
    if result.notes_url:
        console.print(f"  release notes: {result.notes_url}")
    if check_only:
        console.print(
            "[dim]--check passed; re-run without --check to apply.[/dim]"
        )
        return
    if result.manifest is None:
        console.print("[red]error: manifest missing from check result[/red]")
        raise typer.Exit(code=2)
    try:
        installed_at = updater.apply_update(result.manifest)
    except updater.UpdaterError as exc:
        console.print(f"[red]update failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]installed asset at[/green] {installed_at}\n"
        "[dim]restart yt-uniq to pick up the new version.[/dim]"
    )
    sys.exit(0)
