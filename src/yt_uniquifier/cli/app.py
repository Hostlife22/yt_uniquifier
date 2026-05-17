import typer

from yt_uniquifier.cli.cmd_probe import probe_cmd

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Re-encode owned/licensed video with controlled micro-transforms "
        "for YouTube re-upload. Intended for legitimate use (your own content, "
        "fair-use derivatives, re-cuts). Not for evading rights-holder detection."
    ),
)


@app.command()
def version() -> None:
    """Print version."""
    from yt_uniquifier import __version__

    typer.echo(__version__)


app.command("probe")(probe_cmd)


@app.command()
def run() -> None:
    """Run uniquification on an input. (Implemented in Phase 2 — see specs/02.md)"""
    raise typer.Exit(code=2)
