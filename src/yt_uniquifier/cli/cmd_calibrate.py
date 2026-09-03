"""`yt-uniq calibrate` — iteratively tune a base profile for a specific input."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from yt_uniquifier.core.calibration.loop import (
    CalibrationMetric,
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
        help="Maximum internal source/derivative similarity diagnostic (0..1).",
    ),
    min_quality: float = typer.Option(
        88.0, "--min-quality",
        help="Minimum quality score (VMAF / SSIM × 100 / pHash × 100, unified scale).",
    ),
    iterations: int = typer.Option(5, "--iterations"),
    clip_sec: float = typer.Option(
        60.0, "--clip-sec",
        help="Total probe seconds spread across source start, middle, and end.",
    ),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    work_dir: Path = typer.Option(
        Path(".yt_uniq_calib"), "--work-dir",
        help="Per-iteration scratch area.",
    ),
    metric: str = typer.Option(
        "chromaprint", "--metric",
        help=(
            "Local similarity diagnostic used by the bounded search. "
            "'chromaprint' uses the v0.5 audio-fingerprint predictor (needs fpcalc); "
            "'sscd' uses the SSCD copy-detection embedding (needs the [ml] extra)."
        ),
    ),
) -> None:
    """Search bounded intensity against similarity and quality constraints.

    Exit codes:
      0  converged within --iterations
      1  profile load / calibration error
      2  did not converge within --iterations (tuned profile still written)
    """
    if metric not in ("chromaprint", "sscd"):
        console.print(
            f"[red]error:[/red] --metric must be 'chromaprint' or 'sscd', "
            f"got {metric!r}"
        )
        raise typer.Exit(code=1)
    metric_typed: CalibrationMetric = (
        "sscd" if metric == "sscd" else "chromaprint"
    )

    try:
        base_profile = load_profile(base)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    target = CalibrationTarget(
        max_self_match=target_match,
        min_quality=min_quality,
        max_iterations=iterations,
        test_clip_sec=clip_sec,
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("factor", justify="right")
    table.add_column("self_match", justify="right")
    table.add_column("quality", justify="right")
    table.add_column("note")

    def on_step(s: CalibrationStep) -> None:
        quality_str = "—"
        if s.quality is not None:
            quality_str = f"{s.quality:.1f} ({s.quality_metric})"
        note = s.note or ""
        # Trim long notes so the table stays readable.
        if len(note) > 60:
            note = note[:57] + "..."
        table.add_row(
            str(s.iteration),
            f"{s.intensity_factor:.2f}",
            f"{s.self_match:.4f}",
            quality_str,
            note,
        )

    try:
        result = calibrate(
            input, base_profile, target,
            work_dir=work_dir, encoder_override=encoder_override,
            on_step=on_step, metric=metric_typed,
        )
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(table)
    console.print(f"[dim]metric: {metric_typed}[/dim]")
    status = "[green]converged[/green]" if result.converged else "[yellow]not converged[/yellow]"
    quality_str = ""
    if result.final_quality is not None:
        quality_str = (
            f" · quality={result.final_quality:.1f} ({result.final_quality_metric})"
        )
    console.print(
        f"{status} · final self_match={result.final_self_match:.4f}"
        + quality_str
        + f" · factor={result.factor:.2f}"
    )
    if result.note:
        console.print(f"[dim]{result.note}[/dim]")

    out.parent.mkdir(parents=True, exist_ok=True)
    dump_profile(result.profile, out)
    console.print(f"Wrote: {out}")

    if not result.converged:
        raise typer.Exit(code=2)
