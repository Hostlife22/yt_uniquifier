"""`yt-uniq qa <input> <output>` — run QA on a pre-existing pair."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.core.qa.report import build_report, render_html, verdict, write_json

console = Console()

_COLOR = {"invalid": "magenta", "green": "green", "yellow": "yellow", "red": "red"}


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
    no_cid_predict: bool = typer.Option(
        False, "--no-cid-predict",
        help="Skip the legacy per-chunk similarity heuristic.",
    ),
    vs_corpus: bool = typer.Option(
        False, "--vs-corpus",
        help="Also search the local corpus for matches against the output.",
    ),
    corpus_dir: Path | None = typer.Option(None, "--corpus-dir"),
    fast_qa: bool = typer.Option(
        False, "--fast-qa",
        help="Cheaper QA: skip VMAF, halve sample count. Same flag as `yt-uniq run`.",
    ),
    sscd: bool = typer.Option(
        False, "--sscd",
        help=(
            "Add SSCD representation-similarity diagnostics. Requires the `[ml]` "
            "extra (torch + torchvision) and downloads ~80 MB of weights "
            "on first use. Adds ~5-10 s per call on CPU."
        ),
    ),
    sscd_frames: int = typer.Option(
        32, "--sscd-frames",
        help="Frame grid size used by --sscd (default 32, more = more precise + slower).",
    ),
    plan_json: Path | None = typer.Option(
        None,
        "--plan-json",
        exists=True,
        dir_okay=False,
        readable=True,
        help=(
            "Exact serialized Plan provenance for registered metrics. Without it, "
            "standalone QA reports only the unchanged raw metrics."
        ),
    ),
    registration_segment_sec: float = typer.Option(
        600.0,
        "--registration-segment-sec",
        min=1.0,
        max=86400.0,
        help="Segment target used by the original run represented by --plan-json.",
    ),
) -> None:
    """Compute similarity metrics for an (input, output) pair."""
    if fast_qa:
        # --fast-qa is shorthand: equivalent to --no-vmaf and --samples 60.
        # Explicit --samples wins if the caller set a non-default value.
        no_vmaf = True
        if samples == 120:
            samples = 60
    try:
        plan: Plan | None = None
        if plan_json is not None:
            try:
                plan = Plan.model_validate_json(plan_json.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                raise YtUniquifierError(f"invalid --plan-json: {exc}") from exc
            if input.stat().st_size != plan.source.size_bytes:
                raise YtUniquifierError(
                    "--plan-json source size does not match the supplied input"
                )
            plan = plan.model_copy(update={
                "source": plan.source.model_copy(update={"path": input}),
            })
        corpus = Corpus(corpus_dir) if vs_corpus else None
        report = build_report(
            input, output,
            plan=plan,
            samples=samples,
            run_vmaf=not no_vmaf,
            run_audio_fp=not no_audio_fp,
            run_ssim=not no_ssim,
            predict_cid=not no_cid_predict,
            vs_corpus=corpus,
            compute_sscd=sscd,
            sscd_frame_count=sscd_frames,
            run_registered=plan is not None,
            registration_target_segment_sec=registration_segment_sec,
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
    console.print(f"[{colour}]Overall output status: {v.band.upper()}[/{colour}]")
    console.print(f"  Correctness:       {v.correctness.upper()}")
    console.print(f"  Quality:           {v.quality.upper()}")
    console.print(f"  Visual similarity: {v.visual_similarity.upper()} (diagnostic)")
    for reason in v.reasons:
        console.print(f"  • {reason}")
    if report.phash_samples > 0:
        console.print(f"  pHash similarity: {report.phash_similarity:.4f}")
    if report.vmaf_mean is not None:
        console.print(f"  VMAF mean:        {report.vmaf_mean:.2f}")
    if report.ssim_mean is not None:
        console.print(f"  SSIM mean:        {report.ssim_mean:.4f}")
    if report.vmaf_registered_mean is not None:
        console.print(f"  VMAF registered:  {report.vmaf_registered_mean:.2f}")
    if report.ssim_registered_mean is not None:
        console.print(f"  SSIM registered:  {report.ssim_registered_mean:.4f}")
    if report.sscd_registered_mean is not None:
        console.print(f"  SSCD registered:  {report.sscd_registered_mean:.4f}")
    if report.audio_fp_registered_hamming_per_frame is not None:
        console.print(
            "  Audio registered: "
            f"{report.audio_fp_registered_hamming_per_frame:.2f} bits/frame"
        )
    if report.audio_fp_similarity is not None:
        console.print(f"  Audio FP:         {report.audio_fp_similarity:.4f}")
    if report.cid_predict_self is not None:
        console.print(f"  Legacy weighted similarity: {report.cid_predict_self:.4f}")
    if report.sscd_mean is not None:
        from yt_uniquifier.core.qa.sscd import sscd_band

        band = sscd_band(report.sscd_mean)
        band_label = {
            "high": "high similarity",
            "caution": "moderate similarity",
            "clean": "low similarity",
        }[band]
        console.print(
            f"  SSCD similarity:  {report.sscd_mean:.4f} "
            f"({band_label})  "
            f"min: {report.sscd_min:.4f}"
            if report.sscd_min is not None
            else f"  SSCD mean:        {report.sscd_mean:.4f} "
                 f"({band_label})"
        )
    if report.corpus_matches:
        console.print(
            f"  Corpus matches:   {len(report.corpus_matches)} above threshold"
        )
        for m in report.corpus_matches[:3]:
            console.print(f"    [red]✗[/red] {m['path']} — combined {m['combined']:.3f}")
    for n in report.notes:
        console.print(f"  [dim]note:[/dim] {n}")
    console.print(f"Wrote: {json_path}")
    console.print(f"Wrote: {html_path}")
