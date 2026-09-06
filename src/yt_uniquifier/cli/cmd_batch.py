"""`yt-uniq batch <dir> --profile … --out <dir>` — sequential batch processing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.cli.progress_view import make_batch_progress
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report, render_html, write_json

console = Console()


@dataclass
class _BatchResult:
    input: Path
    output: Path | None
    ok: bool
    note: str | None = None


def batch_cmd(
    inputs_dir: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, readable=True,
        help="Directory to scan for inputs.",
    ),
    profile: Path = typer.Option(..., "--profile", help="YAML profile file."),
    output_dir: Path = typer.Option(..., "--out", help="Output directory."),
    pattern: str = typer.Option("*.mp4", "--pattern", help="Glob pattern."),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    work_dir: Path = typer.Option(
        Path(".yt_uniq_work"), "--work-dir", help="Per-input work area root."
    ),
    fast_qa: bool = typer.Option(False, "--fast-qa"),
    no_qa: bool = typer.Option(False, "--no-qa"),
    keep_segments: bool = typer.Option(False, "--keep-segments"),
    continue_on_error: bool = typer.Option(
        True, "--continue-on-error/--stop-on-error",
        help="Skip files that fail and keep going (default), or stop at the first error.",
    ),
) -> None:
    """Process every matching file in inputs_dir sequentially."""
    inputs = sorted(inputs_dir.glob(pattern))
    if not inputs:
        console.print(f"[yellow]No inputs match {pattern!r} in {inputs_dir}[/yellow]")
        # Exit 2 signals "no inputs matched" — distinct from success (0) and
        # from a real processing failure (1). Distributed harnesses check
        # this code to distinguish a no-op from a completed batch.
        raise typer.Exit(code=2)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        prof = load_profile(profile)
    except YtUniquifierError as exc:
        console.print(f"[red]profile error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    results: list[_BatchResult] = []
    try:
        with make_batch_progress(console) as bar:
            task_id = bar.add_task("batch", total=len(inputs))
            for src in inputs:
                console.print(f"[bold]→ {src.name}[/bold]")
                res = _process_one(
                    src, prof, output_dir, encoder_override,
                    work_dir, fast_qa=fast_qa, no_qa=no_qa,
                    keep_segments=keep_segments,
                )
                results.append(res)
                bar.advance(task_id)
                if not res.ok and not continue_on_error:
                    break
    except KeyboardInterrupt:
        # POSIX convention: 128 + SIGINT(2) = 130. Files completed before
        # Ctrl+C stay in `results`; the user can re-run with the same
        # output_dir and the per-file work_dir/state.json resumes.
        _print_summary(results)
        console.print(
            "[yellow]cancelled (Ctrl+C); processed files preserved[/yellow]",
        )
        raise typer.Exit(code=130) from None

    _print_summary(results)
    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


def _process_one(
    src: Path,
    profile: Profile,
    output_dir: Path,
    encoder_override: str | None,
    work_root: Path,
    *,
    fast_qa: bool,
    no_qa: bool,
    keep_segments: bool,
) -> _BatchResult:
    out = output_dir / (src.stem + ".uniq.mp4")
    try:
        plan = build_plan(src, profile, encoder_override)
    except YtUniquifierError as exc:
        return _BatchResult(input=src, output=None, ok=False, note=str(exc))

    options = RunOptions(
        work_dir=work_root / plan.plan_hash,
        output=out,
        encoder_override=encoder_override,
        keep_segments=keep_segments,
        enforce_preflight=True,
    )
    try:
        summary = run_full(plan, options, on_event=lambda _e: None)
    except YtUniquifierError as exc:
        return _BatchResult(input=src, output=None, ok=False, note=str(exc))

    if not no_qa:
        try:
            report = build_report(
                src, out,
                plan=summary.plan,
                samples=60 if fast_qa else 120,
                run_vmaf=not fast_qa,
                run_registered=True,
                registration_target_segment_sec=options.target_segment_sec,
                # run_full already performed the mandatory complete A/V decode.
                verify_decode=False,
                decode_evidence=summary.decode_evidence,
            )
            write_json(report, out.with_suffix(out.suffix + ".qa.json"))
            render_html(report, summary.plan, out.with_suffix(out.suffix + ".qa.html"))
        except Exception as exc:  # noqa: BLE001 - QA must never break the batch
            return _BatchResult(input=src, output=out, ok=True,
                                note=f"qa failed: {exc}")

    return _BatchResult(input=src, output=out, ok=True)


def _print_summary(results: list[_BatchResult]) -> None:
    ok = sum(1 for r in results if r.ok)
    bad = len(results) - ok
    console.print()
    console.print(f"[bold]Summary:[/bold] {ok} ok, {bad} failed")
    for r in results:
        if r.ok:
            note = f" ({r.note})" if r.note else ""
            console.print(f"  [green]✓[/green] {r.input.name} → {r.output}{note}")
        else:
            console.print(f"  [red]✗[/red] {r.input.name} — {r.note}")
