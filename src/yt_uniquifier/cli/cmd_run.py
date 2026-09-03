"""`yt-uniq run` — full pipeline with segmentation + resume."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.cli.progress_view import make_run_progress
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
    profile: str = typer.Option(
        ..., "--profile",
        help=(
            "YAML profile file path, OR ``auto`` to pick a shipped "
            "profile from the source's resolution/aspect/HDR fingerprint."
        ),
    ),
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
    new_variant: bool = typer.Option(
        False, "--new-variant",
        help="Ignore any stored run_seed in state.json and roll a fresh one "
             "(produces a different output even if work-dir has prior state).",
    ),
    workers: int = typer.Option(
        1, "--workers",
        help="Parallel segment workers (libx264 / libx265 only; GPU encoders "
             "stay sequential).",
    ),
    no_progress: bool = typer.Option(False, "--no-progress", help="Suppress progress bar."),
    sanitize_bitstream: bool = typer.Option(
        False, "--sanitize-bitstream",
        help="Optional H.264 interoperability pass via libx264. Adds "
             "~30-60 min wall time and generation loss on long sources; "
             "not recommended for quality-first output. No-op for libx264 "
             "and refused for HDR, HEVC, or AV1 contracts.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Build the Plan, run preflight, print the segment shape, "
             "filter_complex, encoder pick, disk estimate, and ETA — "
             "then exit without spawning ffmpeg.",
    ),
    accept_watermark_risk: bool = typer.Option(
        False, "--accept-watermark-risk",
        help="Attest that you own / are licensed to re-upload this "
             "content. Skips the watermark / station-ID guardrail "
             "added in v1.3.0 Task 30. Equivalent profile-level "
             "opt-out: skip_watermark_check: true.",
    ),
) -> None:
    """Run uniquification on an input."""
    try:
        profile_path = _resolve_profile_option(input, profile, console)
        prof = load_profile(profile_path)
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
            force_new_variant=new_variant,
            workers=workers,
            sanitize_bitstream=sanitize_bitstream,
            accept_watermark_risk=accept_watermark_risk,
        )

        if dry_run:
            _print_dry_run_report(plan, options, console)
            return

        if no_progress:
            run_full(plan, options, on_event=lambda _e: None, cancel_token=cancel)
        else:
            with make_run_progress(console) as progress:
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
    except KeyboardInterrupt:
        # POSIX convention: 128 + SIGINT(2) = 130. Ctrl+C must leave
        # state.json intact so the user can re-run and resume from the
        # last completed segment — checkpoint flushes happen on every
        # segment mark so no extra cleanup is needed here.
        cancel.cancel()
        console.print("[yellow]cancelled (Ctrl+C); state preserved for resume[/yellow]")
        raise typer.Exit(code=130) from None
    except YtUniquifierError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _resolve_profile_option(
    input_path: Path, profile_arg: str, console: Console,
) -> Path:
    """v1.1.0 Task 21: resolve ``--profile auto`` to a shipped YAML.

    ``profile_arg`` is one of:
      * ``"auto"``  — probe the source and pick via core.recommender;
      * any other string — treated as a path to a YAML profile file.

    Returns the resolved ``Path`` ready to hand to ``load_profile``.
    Raises a Typer Exit with a clear error if ``auto`` is requested but
    the shipped slug doesn't exist (would only happen on a corrupted
    install).
    """
    if profile_arg != "auto":
        return Path(profile_arg)

    from yt_uniquifier.core.probe import probe as probe_file
    from yt_uniquifier.core.recommender import explain

    console.print("[dim]profile auto: probing source…[/dim]")
    source = probe_file(input_path)
    rec = explain(source)
    console.print(f"[dim]profile auto: picked [/dim][cyan]{rec.slug}[/cyan]"
                  f"[dim] — {rec.reason}[/dim]")

    shipped = (
        Path(__file__).resolve().parents[1] / "profiles" / f"{rec.slug}.yaml"
    )
    if not shipped.exists():
        console.print(
            f"[red]profile auto: shipped slug {rec.slug!r} not found "
            f"at {shipped} (corrupted install?)[/red]",
        )
        raise typer.Exit(code=1)
    return shipped


def _print_dry_run_report(
    plan: Plan, options: RunOptions, console: Console,
) -> None:
    """v1.1.0 Task 20: print the run shape without spawning ffmpeg.

    Pulls in preflight, segment plan, filter_complex shape, and a rough
    ETA so the user can sanity-check a long run before committing the
    disk space and wall clock to it.
    """
    from yt_uniquifier.core.pipeline import build_video_segment_command_fused
    from yt_uniquifier.core.preflight import preflight
    from yt_uniquifier.core.segmenter import plan_segments

    findings = preflight(
        plan.source, plan, plan.encoder, work_dir=options.work_dir,
        accept_watermark_risk=options.accept_watermark_risk,
    )
    blocking = [f for f in findings if f.severity == "fail"]
    warnings = [f for f in findings if f.severity == "warn"]

    console.print("[bold]dry-run summary[/bold]")
    console.print(f"  input         : {plan.source.path}")
    console.print(f"  duration      : {plan.source.duration_sec:.1f} s")
    console.print(f"  encoder       : {plan.encoder.name} ({plan.encoder.vendor})")
    console.print(f"  profile       : {plan.profile.name}")
    console.print(f"  output        : {options.output}")
    console.print(f"  work_dir      : {options.work_dir}")
    console.print(f"  plan_hash     : {plan.plan_hash}")

    try:
        segments = plan_segments(plan, options.target_segment_sec)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]segment plan failed:[/red] {exc}")
        return
    console.print(f"  segments      : {len(segments)} "
                  f"(target {options.target_segment_sec:.0f} s each)")
    if segments:
        first = segments[0]
        console.print(
            f"  first seg     : [{first.start_sec:.2f}, {first.end_sec:.2f}] "
            f"({first.end_sec - first.start_sec:.2f} s)",
        )

    # Disk estimate — same heuristic as preflight (bitrate × duration × 1.3).
    bitrate = (
        plan.source.video[0].bit_rate
        if plan.source.video and plan.source.video[0].bit_rate
        else 8_000_000
    )
    est_bytes = int(plan.source.duration_sec * (bitrate / 8.0) * 1.3)
    console.print(f"  disk estimate : ~{est_bytes / (1024**3):.2f} GiB")

    # ETA. v1.2.0 Task 28 — prefer the PGO cache prediction (calibrated
    # against this machine's actual encoder throughput); fall back to the
    # rough heuristic only when there's no historical data for the
    # (resolution, codec, encoder) key yet.
    from yt_uniquifier.core import pgo as _pgo
    eta_source = "heuristic"
    if plan.source.video:
        v = plan.source.video[0]
        prediction = _pgo.predict(
            source_width=v.width, source_height=v.height,
            codec=plan.profile.target_codec,
            encoder_kind=plan.encoder.vendor,
        )
    else:
        prediction = None
    if prediction is not None:
        eta_sec = prediction.eta_seconds(plan.source.duration_sec)
        eta_source = "PGO cache"
    elif plan.encoder.vendor in {"nvenc", "qsv", "amf", "videotoolbox", "vulkan"}:
        eta_sec = plan.source.duration_sec * 0.7
    else:
        eta_sec = plan.source.duration_sec * 2.0
    console.print(f"  eta ({eta_source}): ~{eta_sec / 60:.1f} min")

    # filter_complex of the first segment — identical shape across all
    # segments, so one sample is enough for the user to eyeball.
    if segments:
        try:
            cmd = build_video_segment_command_fused(
                plan, segments[0], plan.source.path,
                options.work_dir / "dryrun_seg.mkv",
            )
            console.print("  filter_complex:")
            console.print(f"    {cmd.filter_complex}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  filter_complex: [yellow]build failed: {exc}[/yellow]")

    if warnings:
        console.print(f"[yellow]preflight warnings ({len(warnings)}):[/yellow]")
        for f in warnings:
            console.print(f"  - {f.code}: {f.message}")
    if blocking:
        console.print(f"[red]preflight blockers ({len(blocking)}):[/red]")
        for f in blocking:
            console.print(f"  - {f.code}: {f.message}")
    else:
        console.print("[green]preflight: no blockers[/green]")
    console.print("[dim]dry-run: no ffmpeg processes spawned[/dim]")


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


_warned_unparseable_time = False


def _extract_out_time_us(ev: RunEvent) -> int:
    payload = ev.payload
    raw_us = payload.get("out_time_us")
    if isinstance(raw_us, str):
        try:
            return int(raw_us)
        except ValueError:
            _warn_unparseable_time_once(raw_us)
    raw_ms = payload.get("out_time_ms")
    if isinstance(raw_ms, str):
        try:
            return int(raw_ms) * 1000
        except ValueError:
            _warn_unparseable_time_once(raw_ms)
    return 0


def _warn_unparseable_time_once(raw: str) -> None:
    """Emit one warning per process for unparseable ffmpeg progress timestamps.

    Silent fallback to 0 made progress bars stall with no diagnostics. We
    only warn once because a malformed format typically repeats every
    progress tick, and a stream of identical warnings drowns the bar.
    """
    global _warned_unparseable_time
    if not _warned_unparseable_time:
        _warned_unparseable_time = True
        console.print(
            f"[yellow]warning:[/yellow] unparseable ffmpeg progress "
            f"timestamp {raw!r}; progress may stall."
        )
