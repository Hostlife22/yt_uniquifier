"""Build ffmpeg `-metadata` / `-map_metadata` arguments for the final output."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from yt_uniquifier.core.models import Plan, Profile, SourceMeta


def resolve_title(template: str, source: SourceMeta, profile: Profile, plan_hash: str) -> str:
    """Substitute the supported template tokens.

    Supported: {stem}, {date} (YYYY-MM-DD), {profile}, {hash8}.
    """
    return (
        template
        .replace("{stem}", source.path.stem)
        .replace("{date}", datetime.now(UTC).strftime("%Y-%m-%d"))
        .replace("{profile}", profile.name)
        .replace("{hash8}", plan_hash[:8])
    )


def build_metadata_args(
    plan: Plan,
    *,
    title_template: str | None = None,
    creation_time: datetime | None = None,
) -> list[str]:
    """Return ffmpeg args that strip source metadata and add a clean set."""
    when = (creation_time or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    # Do NOT write a custom encoder=… tag — ffmpeg's muxer writes its own
    # encoder=Lavf<version>, indistinguishable from any other ffmpeg output.
    # A tool-specific string would fingerprint the file as tool-generated;
    # mirror the policy in pipeline.py:FilterGraph._metadata_args.
    args: list[str] = [
        "-map_metadata", "-1",
        "-metadata", f"creation_time={when}",
    ]
    if title_template:
        title = resolve_title(title_template, plan.source, plan.profile, plan.plan_hash)
        args += ["-metadata", f"title={title}"]
    # Re-attach language tags for streams that had them.
    for i, a in enumerate(plan.source.audio):
        if a.language:
            args += [f"-metadata:s:a:{i}", f"language={a.language}"]
    return args


def output_log_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".log")
