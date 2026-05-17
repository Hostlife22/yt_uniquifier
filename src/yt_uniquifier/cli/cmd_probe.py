"""`yt-uniq probe` — print SourceMeta JSON for a file, or list working encoders."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from yt_uniquifier.core.encoder import detect_encoders
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.probe import probe as probe_file


def probe_cmd(
    path: Path | None = typer.Argument(
        None,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Media file to probe. Required unless --encoders.",
    ),
    encoders: bool = typer.Option(
        False, "--encoders", help="List available encoders instead of probing a file."
    ),
    force_refresh: bool = typer.Option(
        False, "--refresh", help="Bypass encoder-cache and re-probe."
    ),
) -> None:
    """Probe an input file or list available encoders."""
    try:
        if encoders:
            cands = detect_encoders(force=force_refresh)
            out: object = [c.model_dump() for c in cands]
        else:
            if path is None:
                raise typer.BadParameter("path required unless --encoders is set")
            out = probe_file(path).model_dump(mode="json")
    except YtUniquifierError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(out, indent=2, default=str))
