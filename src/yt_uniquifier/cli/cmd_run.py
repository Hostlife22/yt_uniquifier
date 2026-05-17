"""`yt-uniq run` — single-pass uniquification of one input file.

This Phase-2 command does not yet implement segmentation/resume; it builds
a single ffmpeg invocation for the whole file. Segmentation lands in Phase 3.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.models import Plan, Profile, TransformConfig
from yt_uniquifier.core.pipeline import FilterGraph, compute_plan_hash
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.runner import CancelToken, RunEvent
from yt_uniquifier.core.runner import run as run_ffmpeg

console = Console()


def run_cmd(
    input: Path = typer.Argument(  # noqa: A002 - matches user-facing CLI naming
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
    no_progress: bool = typer.Option(False, "--no-progress", help="Suppress progress bar."),
) -> None:
    """Run uniquification on an input."""
    try:
        source = probe_file(input)
        prof = load_profile(profile)
        if b_video is not None:
            prof = _inject_b_video(prof, b_video)
        enc = pick_encoder(
            detect_encoders(),
            prefer=[encoder_override] if encoder_override else None,
            codec=prof.target_codec,
        )
        plan = Plan(
            source=source,
            profile=prof,
            encoder=enc,
            plan_hash=compute_plan_hash(source, prof, enc),
        )

        total_us = int(source.duration_sec * 1_000_000)
        progress = (
            Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
                transient=False,
            )
            if not no_progress
            else None
        )
        cancel = CancelToken()

        graph = FilterGraph(plan, output)
        console.print(f"[dim]Encoder: {enc.name} ({enc.vendor})[/dim]")
        console.print("[dim]Measuring loudness (one-pass scan)…[/dim]")
        built = graph.build()

        output.parent.mkdir(parents=True, exist_ok=True)
        log_path = output.with_suffix(output.suffix + ".log")

        if progress is not None:
            with progress:
                task_id = progress.add_task("encoding", total=total_us)

                def on_event(ev: RunEvent) -> None:
                    if ev.kind != "progress":
                        return
                    done_us = _extract_out_time_us(ev)
                    progress.update(task_id, completed=min(done_us, total_us))

                run_ffmpeg(
                    built,
                    output=output,
                    on_event=on_event,
                    cancel_token=cancel,
                    log_path=log_path,
                )
        else:
            run_ffmpeg(
                built,
                output=output,
                on_event=lambda _e: None,
                cancel_token=cancel,
                log_path=log_path,
            )

        console.print(f"[green]Done:[/green] {output}")
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _inject_b_video(profile: Profile, b_video: Path) -> Profile:
    """Return a copy of profile with b_video_path set on video.blend_b transforms."""
    new_transforms: list[TransformConfig] = []
    for tc in profile.transforms:
        if tc.id == "video.blend_b":
            new_params = dict(tc.params or {})
            new_params.setdefault("b_video_path", str(b_video))
            new_transforms.append(TransformConfig(
                id=tc.id, enabled=tc.enabled, params=new_params
            ))
        else:
            new_transforms.append(tc)
    return profile.model_copy(update={"transforms": new_transforms})


def _extract_out_time_us(ev: RunEvent) -> int:
    """Parse out_time_us (preferred) or out_time_ms from a progress payload."""
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
