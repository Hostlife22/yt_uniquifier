"""Typed exceptions used across the core."""


class YtUniquifierError(Exception):
    """Base for all yt-uniquifier errors."""


class ProbeError(YtUniquifierError):
    """ffprobe failed or returned data we cannot interpret."""


class EncoderError(YtUniquifierError):
    """Encoder detection or selection failed."""


class FfmpegNotFoundError(YtUniquifierError):
    """ffmpeg or ffprobe binary is not on PATH."""


class PipelineError(YtUniquifierError):
    """Filter graph construction or ffmpeg run failed."""


class CheckpointError(YtUniquifierError):
    """Checkpoint state is corrupt or incompatible."""


class PreflightFailure(YtUniquifierError):
    """A preflight finding with severity=fail was raised."""
