"""Public, SemVer-stable surface of ``yt_uniquifier.core``.

Re-exports the contracts that ``docs/api-contracts.md`` marks as
**stable**. Anything not re-exported here is considered internal and
may move without an RFC — see ``docs/versioning.md`` for the policy.

Importing through this module is the only supported way to use the
core programmatically:

    from yt_uniquifier.core import Plan, Profile, RunOptions, run_full

Importing directly from a submodule (e.g. ``from
yt_uniquifier.core.orchestrator import ...``) still works, but those
paths are not covered by the SemVer contract — they can be
reorganised internally between MINOR releases.
"""

from yt_uniquifier.core.models import (
    AudioStream,
    Chapter,
    Container,
    EncoderCandidate,
    EncoderKind,
    EncoderVendor,
    HDRInfo,
    Plan,
    Profile,
    QAReport,
    SegmentationConfig,
    SegmentationMode,
    SourceMeta,
    SubtitleStream,
    TransformConfig,
    VideoStream,
)
from yt_uniquifier.core.orchestrator import (
    RunOptions,
    RunSummary,
    build_plan,
    run_full,
)
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.runner import CancelToken, EventKind, RunEvent, RunResult

__all__ = [
    "AudioStream",
    "CancelToken",
    "Chapter",
    "Container",
    "EncoderCandidate",
    "EncoderKind",
    "EncoderVendor",
    "EventKind",
    "HDRInfo",
    "Plan",
    "Profile",
    "QAReport",
    "RunEvent",
    "RunOptions",
    "RunResult",
    "RunSummary",
    "SegmentationConfig",
    "SegmentationMode",
    "SourceMeta",
    "SubtitleStream",
    "TransformConfig",
    "VideoStream",
    "build_plan",
    "compute_plan_hash",
    "run_full",
]
