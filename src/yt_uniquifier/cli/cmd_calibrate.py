"""`yt-uniq calibrate` — iteratively tune a base profile for a specific input."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from yt_uniquifier.core.calibration.loop import (
    CalibrationStep,
    CalibrationTarget,
    calibrate,
)
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.profile_loader import dump_profile, load_profile

console = Console()


def calibrate_cmd(
    input: Path = typer.Argument(  # noqa: A002
        ..., exists=True, dir_okay=False, readable=True,
        help="Source media to calibrate against.",
    ),
    base: Path = typer.Option(..., "--base", help="Starting profile YAML."),
    out: Path = typer.Option(..., "--out", help="Where to write the tuned profile."),
    target_match: float = typer.Option(
        0.2, "--target",
        help="Target maximum predicted Content ID self-match (0..1).",
    ),
    min_vmaf: float = typer.Option(88.0, "--min-vmaf"),
    iterations: int = typer.Option(5, "--iterations"),
    clip_sec: float = typer.Option(
        60.0, "--clip-sec",
        help="Test clip length in seconds (calibration is run on this prefix).",
    ),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    work_dir: Path = typer.Option(
        Path(".yt_uniq_calib"), "--work-dir",
        help="Per-iteration scratch area.",
    ),
) -> None:
    """Bisect intensity until predicted self-match drops below --target."""
    try:
        base_profile = load_profile(base)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    target = CalibrationTarget(
        max_self_match=target_match,
        min_vmaf=min_vmaf,
        max_iterations=iterations,
        test_clip_sec=clip_sec,
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("factor", justify="right")
    table.add_column("self_match", justify="right")
    table.add_column("vmaf", justify="right")
    table.add_column("note")

    def on_step(s: CalibrationStep) -> None:
        table.add_row(
            str(s.iteration),
            f"{s.intensity_factor:.2f}",
            f"{s.self_match:.4f}",
            f"{s.vmaf:.1f}" if s.vmaf is not None else "—",
            s.note or "",
        )

    try:
        result = calibrate(
            input, base_profile, target,
            work_dir=work_dir, encoder_override=encoder_override,
            on_step=on_step,
        )
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(table)
    status = "[green]converged[/green]" if result.converged else "[yellow]not converged[/yellow]"
    console.print(
        f"{status} · final self_match={result.final_self_match:.4f}"
        + (f" · vmaf={result.final_vmaf:.1f}" if result.final_vmaf is not None else "")
        + f" · factor={result.factor:.2f}"
    )
    if result.note:
        console.print(f"[dim]{result.note}[/dim]")

    dump_profile(result.profile, out)
    console.print(f"Wrote: {out}")

    if not result.converged:
        raise typer.Exit(code=2)
