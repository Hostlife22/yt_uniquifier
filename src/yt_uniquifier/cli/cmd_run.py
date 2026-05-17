"""`yt-uniq run` — full pipeline with segmentation + resume."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Plan, Profile, TransformConfig
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.core.runner import CancelToken, RunEvent

console = Console()


def run_cmd(
    input: Path = typer.Argument(  # noqa: A002
        ..., exists=True, dir_okay=False, readable=True, help="Source media file."
    ),
    profile: Path = typer.Option(..., "--profile", help="YAML profile file."),
    output: Path = typer.Option(..., "--out", help="Destination file path."),
    encoder_override: str | None = typer.Option(
        None, "--encoder", help="Force a specific ffmpeg encoder (e.g. libx264)."
    ),
    b_video: Path | None = typer.Option(
        None, "--b-video", help="Path to B-video for video.blend_b transform."
    ),
    work_dir: Path = typer.Option(
        Path(".yt_uniq_work"), "--work-dir",
        help="Directory for segments + state.json (enables resume).",
    ),
    target_segment_sec: float = typer.Option(
        600.0, "--segment-sec", help="Target segment length in seconds."
    ),
    keep_segments: bool = typer.Option(
        False, "--keep-segments", help="Don't delete segment files after concat."
    ),
    no_preflight: bool = typer.Option(
        False, "--no-preflight", help="Skip preflight enforcement (warnings only)."
    ),
    no_qa: bool = typer.Option(False, "--no-qa", help="Skip auto QA report after the run."),
    fast_qa: bool = typer.Option(
        False, "--fast-qa", help="Cheaper QA: skip VMAF, halve sample count."
    ),
    no_progress: bool = typer.Option(False, "--no-progress", help="Suppress progress bar."),
) -> None:
    """Run uniquification on an input."""
    try:
        prof = load_profile(profile)
        if b_video is not None:
            prof = _inject_b_video(prof, b_video)
        plan = build_plan(input, prof, encoder_override)

        total_us = int(plan.source.duration_sec * 1_000_000)
        console.print(f"[dim]Encoder: {plan.encoder.name} ({plan.encoder.vendor})[/dim]")
        cancel = CancelToken()
        options = RunOptions(
            work_dir=work_dir / plan.plan_hash,
            output=output,
            encoder_override=encoder_override,
            target_segment_sec=target_segment_sec,
            keep_segments=keep_segments,
            enforce_preflight=not no_preflight,
        )

        if no_progress:
            run_full(plan, options, on_event=lambda _e: None, cancel_token=cancel)
        else:
            with Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
                transient=False,
            ) as progress:
                task_id = progress.add_task("encoding", total=total_us)
                # Track progress across segments by summing per-segment out_time.
                seg_offsets: dict[int, int] = {}

                def on_event(ev: RunEvent) -> None:
                    if ev.kind != "progress":
                        return
                    seg = ev.payload.get("segment")
                    out_us = _extract_out_time_us(ev)
                    if isinstance(seg, int):
                        seg_offsets[seg] = out_us
                    total = sum(seg_offsets.values())
                    progress.update(task_id, completed=min(total, total_us))

                run_full(plan, options, on_event=on_event, cancel_token=cancel)

        console.print(f"[green]Done:[/green] {output}")
        if not no_qa:
            _run_qa(plan, output, fast=fast_qa)
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _run_qa(plan: Plan, output: Path, *, fast: bool) -> None:
    console.print("[dim]Building QA report…[/dim]")
    report = build_report(
        plan.source.path,
        output,
        samples=60 if fast else 120,
        run_vmaf=not fast,
    )
    json_path = output.with_suffix(output.suffix + ".qa.json")
    html_path = output.with_suffix(output.suffix + ".qa.html")
    write_json(report, json_path)
    render_html(report, plan, html_path)
    console.print(f"[dim]QA report:[/dim] {html_path}")


def _inject_b_video(profile: Profile, b_video: Path) -> Profile:
    new_transforms: list[TransformConfig] = []
    for tc in profile.transforms:
        if tc.id == "video.blend_b":
            new_params = dict(tc.params or {})
            new_params.setdefault("b_video_path", str(b_video))
            new_transforms.append(
                TransformConfig(id=tc.id, enabled=tc.enabled, params=new_params)
            )
        else:
            new_transforms.append(tc)
    return profile.model_copy(update={"transforms": new_transforms})


def _extract_out_time_us(ev: RunEvent) -> int:
    payload = ev.payload
    raw_us = payload.get("out_time_us")
    if isinstance(raw_us, str):
        try:
            return int(raw_us)
        except ValueError:
            pass
    raw_ms = payload.get("out_time_ms")
    if isinstance(raw_ms, str):
        try:
            return int(raw_ms) * 1000
        except ValueError:
            pass
    return 0
