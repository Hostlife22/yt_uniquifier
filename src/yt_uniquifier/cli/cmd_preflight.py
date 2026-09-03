"""`yt-uniq preflight` — validate input + profile before a run."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import has_fail, preflight
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.profile_loader import load_profile

console = Console()

_COLORS = {"ok": "green", "warn": "yellow", "fail": "red"}


def preflight_cmd(
    input: Path = typer.Argument(  # noqa: A002
        ..., exists=True, dir_okay=False, readable=True, help="Source media file."
    ),
    profile: Path = typer.Option(..., "--profile", help="YAML profile file."),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Validate input + profile against YouTube targets and HDR sanity."""
    try:
        source = probe_file(input)
        prof = load_profile(profile)
        enc = pick_encoder(
            detect_encoders(),
            prefer=[encoder_override] if encoder_override else None,
            codec=prof.target_codec,
            require_preferred=encoder_override is not None,
        )
        plan = Plan(
            source=source, profile=prof, encoder=enc,
            plan_hash=compute_plan_hash(source, prof, enc),
        )
        findings = preflight(source, plan, enc)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps([f.model_dump() for f in findings], indent=2))
    else:
        for f in findings:
            colour = _COLORS.get(f.severity, "white")
            console.print(
                f"[{colour}]{f.severity.upper():4}[/{colour}] "
                f"[bold]{f.code}[/bold]: {f.message}"
            )
            if f.suggestion:
                console.print(f"     [dim]→ {f.suggestion}[/dim]")

    if has_fail(findings):
        raise typer.Exit(code=1)
