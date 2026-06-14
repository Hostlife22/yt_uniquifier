"""`yt-uniq subtitles …` — generate SRT sidecars via whisper.cpp.

v0.9.0 R2 / F14 — pre-generation only. The actual burn-in happens
inside the encode via the ``video.subtitles`` transform; running this
command produces the SRT that transform consumes.

Why a separate command rather than auto-generation inside ``yt-uniq
run``: whisper-cpp loads a multi-hundred-MB model on every invocation
and an SRT is worth checking by eye before committing it to a 4-hour
encode. See ``core.subtitles`` for the cache strategy and the model
search heuristic.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.subtitles import (
    SubtitleGenerationError,
    find_default_model,
    generate_srt,
)
from yt_uniquifier.core.transforms._whisper_probe import current_capability

subtitles_app = typer.Typer(
    no_args_is_help=True,
    help="Generate SRT sidecars via whisper.cpp for burn-in transforms.",
)
console = Console()


@subtitles_app.command("capability")
def cmd_capability() -> None:
    """Print detected Whisper / subtitle capabilities."""
    cap = current_capability()
    console.print(f"burn_in_filter:        {cap.burn_in_filter}")
    console.print(f"srt_generator:         {cap.srt_generator or '(none)'}")
    console.print(f"ffmpeg_native_whisper: {cap.ffmpeg_native_whisper}")
    if not cap.has_any_generator:
        console.print(
            "[yellow]no SRT auto-generator available;[/yellow] install "
            "whisper.cpp or provide pre-made SRTs."
        )


@subtitles_app.command("generate")
def cmd_generate(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    model: Path | None = typer.Option(
        None,
        "--model",
        help="Path to a ggml whisper model. Defaults to the first one found "
             "under ~/models, ~/.cache/whisper, or the Homebrew share dir.",
    ),
    language: str | None = typer.Option(
        None,
        "--language", "-l",
        help="ISO 639-1 language code (e.g. en, ru). Defaults to auto-detect.",
    ),
    dest: Path | None = typer.Option(
        None,
        "--out", "-o",
        help="Output SRT path. Defaults to <source>.srt alongside the input.",
    ),
    max_chars_per_line: int = typer.Option(
        42, "--max-chars",
        help="Wrap subtitles to at most this many characters per line.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-run whisper.cpp even if a cached SRT exists.",
    ),
) -> None:
    """Generate an SRT sidecar for ``source`` using whisper.cpp."""
    resolved_model = model
    if resolved_model is None:
        resolved_model = find_default_model()
        if resolved_model is None:
            console.print(
                "[red]error:[/red] no whisper model found and --model not set. "
                "Pass --model /path/to/ggml-base.bin or place one under "
                "~/models/ggml-*.bin."
            )
            raise typer.Exit(code=1)

    try:
        result = generate_srt(
            source,
            resolved_model,
            language=language,
            dest=dest,
            max_chars_per_line=max_chars_per_line,
            force=force,
        )
    except (SubtitleGenerationError, YtUniquifierError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    suffix = " (from cache)" if result.from_cache else ""
    console.print(
        f"[green]generated[/green] {result.path}{suffix} via {result.backend}"
    )
