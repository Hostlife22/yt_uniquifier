"""`yt-uniq qa <input> <output>` — run QA on a pre-existing pair."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.qa.report import build_report, render_html, verdict, write_json

console = Console()

_COLOR = {"green": "green", "yellow": "yellow", "red": "red"}


def qa_cmd(
    input: Path = typer.Argument(  # noqa: A002
        ..., exists=True, dir_okay=False, readable=True, help="Original/reference media."
    ),
    output: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="Re-encoded media to compare."
    ),
    samples: int = typer.Option(120, "--samples", help="Frames sampled for pHash."),
    no_vmaf: bool = typer.Option(False, "--no-vmaf"),
    no_audio_fp: bool = typer.Option(False, "--no-audio-fp"),
    no_ssim: bool = typer.Option(False, "--no-ssim"),
    json_out: Path | None = typer.Option(
        None, "--json", help="Override path for the JSON report."
    ),
    html_out: Path | None = typer.Option(
        None, "--html", help="Override path for the HTML report."
    ),
) -> None:
    """Compute similarity metrics for an (input, output) pair."""
    try:
        report = build_report(
            input, output,
            samples=samples,
            run_vmaf=not no_vmaf,
            run_audio_fp=not no_audio_fp,
            run_ssim=not no_ssim,
        )
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    json_path = json_out or output.with_suffix(output.suffix + ".qa.json")
    html_path = html_out or output.with_suffix(output.suffix + ".qa.html")
    write_json(report, json_path)
    render_html(report, plan=None, dest=html_path)

    v = verdict(report)
    colour = _COLOR.get(v.band, "white")
    console.print(f"[{colour}]Verdict: {v.band.upper()}[/{colour}]")
    for reason in v.reasons:
        console.print(f"  • {reason}")
    console.print(f"  pHash similarity: {report.phash_similarity:.4f}")
    if report.vmaf_mean is not None:
        console.print(f"  VMAF mean:        {report.vmaf_mean:.2f}")
    if report.ssim_mean is not None:
        console.print(f"  SSIM mean:        {report.ssim_mean:.4f}")
    if report.audio_fp_similarity is not None:
        console.print(f"  Audio FP:         {report.audio_fp_similarity:.4f}")
    for n in report.notes:
        console.print(f"  [dim]note:[/dim] {n}")
    console.print(f"Wrote: {json_path}")
    console.print(f"Wrote: {html_path}")
