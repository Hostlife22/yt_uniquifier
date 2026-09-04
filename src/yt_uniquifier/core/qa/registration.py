"""Plan-aware transformed references for additive registered QA metrics."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier import __version__
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.pipeline import build_video_segment_command_fused
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.runner import run as run_ffmpeg
from yt_uniquifier.core.segmenter import _plan_for_segment, concat_segments, plan_segments
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_DEFAULT_REFERENCE_LIMIT_BYTES = 40 * 1024**3


@dataclass(frozen=True)
class TransformedReference:
    path: Path
    cache_key: str
    segments: int


def _sha256_file(path: Path, cancel_token: CancelToken | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            if cancel_token is not None and cancel_token.is_cancelled():
                raise PipelineError("registered reference hashing cancelled by user")
            digest.update(chunk)
    return digest.hexdigest()


def _ffmpeg_version_digest() -> str:
    try:
        output = subprocess.check_output(
            [ffmpeg_bin(), "-hide_banner", "-version"],
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(f"cannot identify FFmpeg for registered QA: {exc}") from exc
    return hashlib.sha256(output).hexdigest()


def reference_provenance_key(
    plan: Plan,
    *,
    target_segment_sec: float,
    cancel_token: CancelToken | None = None,
) -> str:
    """Bind reference provenance to bytes, transforms, seed and toolchain."""
    payload = {
        "schema": 1,
        "source_sha256": _sha256_file(plan.source.path, cancel_token),
        "profile": plan.profile.model_dump(mode="json"),
        "plan_hash": plan.plan_hash,
        "run_seed": plan.run_seed,
        "target_segment_sec": target_segment_sec,
        "tool_version": __version__,
        "ffmpeg_version": _ffmpeg_version_digest(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _reference_limit_bytes() -> int:
    raw = os.environ.get("YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES")
    if raw is None:
        return _DEFAULT_REFERENCE_LIMIT_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise PipelineError(
            "YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES must be an integer"
        ) from exc
    if value < 1:
        raise PipelineError("registered-reference byte limit must be positive")
    return value


def _check_reference_budget(plan: Plan, destination: Path) -> None:
    if not plan.source.video:
        raise PipelineError("registered video QA requires a video stream")
    video = plan.source.video[0]
    # Conservative FFV1 planning estimate (roughly four bits per decoded pixel).
    encoded_estimate = int(
        video.width
        * video.height
        * max(video.fps, 1.0)
        * max(plan.source.duration_sec, 0.001)
        * 0.5
    )
    # Concat publication temporarily coexists with all FFV1 segments. Budget
    # peak workspace, not only the final reference, so a nominally allowed run
    # cannot fill the filesystem during the final copy.
    estimated_peak = encoded_estimate * 2
    configured_limit = _reference_limit_bytes()
    free = shutil.disk_usage(destination.parent).free
    available_limit = min(configured_limit, int(free * 0.80))
    if estimated_peak > available_limit:
        raise PipelineError(
            "registered FFV1 peak workspace estimate exceeds its bounded disk budget: "
            f"estimated={estimated_peak}, allowed={available_limit}. Set "
            "YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES only after provisioning "
            "sufficient temporary storage."
        )


def build_transformed_reference(
    plan: Plan,
    destination: Path,
    *,
    target_segment_sec: float = 600.0,
    cancel_token: CancelToken | None = None,
    progress: Callable[[float], None] | None = None,
) -> TransformedReference:
    """Replay the existing video graph into temporary lossless FFV1 segments."""
    if cancel_token is not None and cancel_token.is_cancelled():
        raise PipelineError("registered reference generation cancelled by user")
    if not (1.0 <= target_segment_sec <= 86400.0):
        raise PipelineError("registered QA segment size must be in [1, 86400] seconds")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _check_reference_budget(plan, destination)
    provenance = reference_provenance_key(
        plan,
        target_segment_sec=target_segment_sec,
        cancel_token=cancel_token,
    )
    segments = plan_segments(plan, target_segment_sec)
    if not segments:
        raise PipelineError("registered QA could not plan reference segments")
    work_dir = destination.parent / "reference-segments"
    work_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    notify = progress or (lambda _fraction: None)
    for index, segment in enumerate(segments):
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError("registered reference generation cancelled by user")
        segment_plan = _plan_for_segment(plan, segment.idx)
        output = work_dir / f"reference_{segment.idx:05d}.mkv"
        command = build_video_segment_command_fused(
            segment_plan,
            segment,
            plan.source.path,
            output,
            _video_encoder_args_override=[
                "-c:v", "ffv1", "-level", "3", "-g", "1", "-slicecrc", "1",
            ],
        )
        run_ffmpeg(
            command,
            output=output,
            cancel_token=cancel_token,
            progress_via_stdout=False,
            log_path=output.with_suffix(".mkv.log"),
        )
        outputs.append(output)
        notify((index + 1) / (len(segments) + 1))

    concat_segments(
        outputs,
        None,
        destination,
        [],
        work_dir=destination.parent / "concat",
        audio_source_indices=[],
        target_duration_sec=plan.source.duration_sec,
        cancel_token=cancel_token,
        on_event=lambda _event: None,
    )
    notify(1.0)
    # The encoded reference digest makes the downstream embedding cache robust
    # even for third-party transforms whose extra input is not represented by a
    # conventional ``*_path`` profile parameter.
    encoded_digest = _sha256_file(destination, cancel_token)
    cache_key = hashlib.sha256(f"{provenance}:{encoded_digest}".encode()).hexdigest()
    return TransformedReference(destination, cache_key, len(segments))


__all__ = [
    "TransformedReference",
    "build_transformed_reference",
    "reference_provenance_key",
]
