"""Data contracts shared across the core. Pydantic v2.

These models are JSON-serializable and contain no ffmpeg-specific logic.
The Plan model is the durable input to the rest of the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ColorTransfer = Literal[
    "bt709",
    "smpte2084",
    "arib-std-b67",
    "bt470bg",
    "smpte170m",
    "iec61966-2-1",
    "unknown",
]
ColorPrimaries = Literal[
    "bt709",
    "bt2020",
    "bt470bg",
    "smpte170m",
    "smpte432",
    "unknown",
]
ColorSpace = Literal[
    "bt709",
    "bt2020nc",
    "bt2020c",
    "bt470bg",
    "smpte170m",
    "unknown",
]
ColorRange = Literal["tv", "pc", "unknown"]
EncoderKind = Literal["h264", "hevc", "av1"]
EncoderVendor = Literal[
    "nvenc", "qsv", "amf", "videotoolbox", "vulkan", "x264", "x265",
]
Container = Literal["mp4", "mov", "mkv"]
AudioTracksOpt = Literal["first", "all"]
SeedStrategy = Literal["fixed", "per_run", "per_file", "divergent"]


class HDRInfo(BaseModel):
    """Color characteristics of a video stream."""

    model_config = ConfigDict(frozen=True)

    is_hdr: bool
    transfer: ColorTransfer = "unknown"
    primaries: ColorPrimaries = "unknown"
    space: ColorSpace = "unknown"
    color_range: ColorRange = "unknown"
    bit_depth: int = 8


class VideoStream(BaseModel):
    """One video stream in the source container."""

    model_config = ConfigDict(frozen=True)

    index: int
    codec: str
    width: int
    height: int
    fps: float
    duration_sec: float
    pix_fmt: str
    bit_rate: int | None = None
    color: HDRInfo
    is_default: bool = False


class AudioStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str | None = None
    bit_rate: int | None = None
    language: str | None = None
    is_default: bool = False


class SubtitleStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    codec: str
    language: str | None = None
    is_image_based: bool = False


class Chapter(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_sec: float
    end_sec: float
    title: str | None = None


class SourceMeta(BaseModel):
    """Result of probing one input file."""

    model_config = ConfigDict(frozen=True)

    path: Path
    container: str
    duration_sec: float
    size_bytes: int
    video: list[VideoStream] = Field(default_factory=list)
    audio: list[AudioStream] = Field(default_factory=list)
    subtitle: list[SubtitleStream] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)


class EncoderCandidate(BaseModel):
    """An ffmpeg encoder we considered for use, with the outcome of a test-run."""

    model_config = ConfigDict(frozen=True)

    name: str
    vendor: EncoderVendor
    codec: EncoderKind
    works: bool
    error: str | None = None
    # v0.3: how many concurrent encode sessions are safe for this encoder
    # on this machine. NVENC consumer drivers cap at 3; pro/Quadro at 8;
    # CPU encoders default to cpu_count() // 2; others 2.
    max_parallel: int = Field(default=1, ge=1, le=64)


class TransformConfig(BaseModel):
    """One entry in a Profile: a transform id with overridden params."""

    model_config = ConfigDict(extra="forbid")

    id: str
    enabled: bool = True
    params: dict[str, object] = Field(default_factory=dict)


SegmentationMode = Literal["keyframe", "scene"]


class SegmentationConfig(BaseModel):
    """How ``plan_segments`` decides where to cut a long source.

    v0.8.0 R3 — adds an opt-in PySceneDetect path. Default is unchanged
    (``keyframe``) so every shipping profile keeps the same segment
    layout. The two scene parameters are PySceneDetect-native; see
    ``core/scene_detect.py`` for the mapping into ``ContentDetector``.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SegmentationMode = "keyframe"
    # ContentDetector.threshold — content-difference value in [0, 255]
    # that triggers a cut. 27.0 is the PySceneDetect documented default
    # and is a good middle ground for film/TV content.
    scene_threshold: float = Field(default=27.0, gt=0.0, le=255.0)
    # Minimum scene length in seconds. Avoids per-shot micro-segments
    # in fast-cut sequences (music videos, trailers) that would defeat
    # the resume-granularity benefit and balloon ffmpeg fork overhead.
    scene_min_length_sec: float = Field(default=2.0, gt=0.0)


class Profile(BaseModel):
    """User-facing recipe loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    transforms: list[TransformConfig] = Field(default_factory=list)
    audio_tracks: AudioTracksOpt | list[int] = "first"
    keep_hdr: bool = False
    output_container: Container = "mp4"
    target_codec: EncoderKind = "h264"
    target_loudness_lufs: float = -14.0
    seed: int | None = None
    # v0.8.0 R3 — segment boundary strategy. Default keyframe matches
    # behaviour from v0.7 and earlier; ``scene`` switches to
    # PySceneDetect with snap-to-keyframe to keep the stream-copy
    # invariant in segmenter.stream_copy_extract.
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    # NEW (v0.2): controls run-time randomization of transform parameters.
    #   fixed     — `seed` used verbatim, every run identical.
    #   per_run   — fresh random seed on each invocation (default).
    #   per_file  — deterministic seed derived from the input path.
    #   divergent — (v0.3.3) fresh per-invocation base seed, AND each segment
    #               derives its own seed from sha256(plan_hash, idx, base_seed).
    #               Adjacent segments get distinct transform-param phases, so
    #               a temporal-aware detector can't lock onto run-level uniformity.
    seed_strategy: SeedStrategy = "per_run"


class Plan(BaseModel):
    """Concrete plan: source + profile + chosen encoder + deterministic hash.

    plan_hash is the resume key. Two runs with identical source content + profile
    + encoder should produce the same hash.
    """

    model_config = ConfigDict(frozen=True)

    source: SourceMeta
    profile: Profile
    encoder: EncoderCandidate
    plan_hash: str
    # NEW (v0.2): per-invocation seed for stochastic transforms.
    # NOT part of plan_hash — the same Plan can be replayed with the same
    # seed on resume. Strategy resolution lives in orchestrator.build_plan.
    run_seed: int = 0


SegmentStatus = Literal["pending", "in_progress", "done", "failed"]


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int
    start_sec: float
    end_sec: float
    status: SegmentStatus = "pending"
    src_path: Path | None = None
    out_path: Path | None = None


class QAReport(BaseModel):
    """Aggregated QA metrics for one (input, output) pair."""

    model_config = ConfigDict(frozen=True)

    input_md5: str
    output_md5: str
    input_size_bytes: int
    output_size_bytes: int
    input_duration_sec: float
    output_duration_sec: float
    phash_samples: int
    phash_distance_min: int
    phash_distance_mean: float
    phash_distance_max: int
    phash_similarity: float
    audio_fp_similarity: float | None = None
    # v0.3.3 — bit-level Hamming distance between paired chromaprint
    # subfingerprints. ≥30 bits/frame ≈ high-confidence non-match.
    audio_fp_hamming_per_frame: float | None = None
    audio_fp_match_confidence: float | None = None
    # v0.4.2 — per-window Hamming variance KPI for `seed_strategy='divergent'`.
    # ≥ 4 bits between adjacent windows on real fixtures = audio varies
    # meaningfully across the timeline.
    audio_fp_hamming_per_window: list[float] | None = None
    audio_fp_hamming_variance: float | None = None
    vmaf_mean: float | None = None
    ssim_mean: float | None = None
    duration_match: bool
    notes: list[str] = Field(default_factory=list)
    # v0.2 — Content ID prediction & corpus matches (optional; only populated
    # when build_report is invoked with predict_cid=True / vs_corpus=True).
    cid_predict_self: float | None = None
    weakest_chunk_sec: tuple[float, float] | None = None
    chunk_similarities: list[dict[str, float]] = Field(default_factory=list)
    corpus_matches: list[dict[str, float | str]] = Field(default_factory=list)
