"""Data contracts shared across the core. Pydantic v2.

These models are JSON-serializable and contain no ffmpeg-specific logic.
The Plan model is the durable input to the rest of the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

if TYPE_CHECKING:
    from yt_uniquifier.core.auxiliary_streams import AuxiliaryStream

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
    # v1.2.0 Task 22 — AV1 software encoders.  Hardware AV1 variants
    # (av1_nvenc, av1_qsv, av1_amf, av1_videotoolbox) reuse the existing
    # nvenc/qsv/amf/videotoolbox vendor tags because their command-line
    # knobs (cq/global_quality/qp_i/b:v) are the same family.
    "libaom", "svtav1",
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
    # Canonical x265 master-display syntax, e.g.
    # G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,1).
    mastering_display: str | None = None
    max_cll: int | None = None
    max_fall: int | None = None
    # Re-encoding dynamic metadata is unsupported unless an encoder/filter
    # chain explicitly proves it can preserve each detected side-data type.
    dynamic_metadata: tuple[str, ...] = ()


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
    title: str | None = None
    is_default: bool = False
    dispositions: tuple[str, ...] = ()


class SubtitleStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    codec: str
    language: str | None = None
    title: str | None = None
    is_image_based: bool = False
    is_default: bool = False
    dispositions: tuple[str, ...] = ()


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
    # Probe-only topology. PrivateAttr deliberately keeps the stable
    # SourceMeta JSON schema and serialized Plan contract unchanged.
    _auxiliary_streams: tuple[AuxiliaryStream, ...] = PrivateAttr(default=())
    # First decoded timestamp of the primary video stream.  Probe-only and
    # private for the same compatibility reason as auxiliary topology.
    _first_video_pts_sec: float | None = PrivateAttr(default=None)


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
    # v0.8.0 R5 — per-segment VMAF target-quality feedback loop
    # (Av1an-style). When set, each segment is scored after the
    # initial encode and, if the score is below ``target_vmaf``,
    # re-encoded with CRF reduced by ``target_vmaf_step`` (cq/qp/
    # global_quality reduced equivalently on hardware encoders).
    # Loops up to ``target_vmaf_max_retries`` times before accepting
    # the best attempt and emitting a ``target_vmaf_failed`` event.
    # Single-host only; ``yt-uniq worker`` (distributed) logs a
    # warning and ignores the field.
    target_vmaf: float | None = Field(default=None, ge=0.0, le=100.0)
    target_vmaf_step: int = Field(default=2, ge=1, le=10)
    target_vmaf_max_retries: int = Field(default=2, ge=0, le=5)
    # NEW (v0.2): controls run-time randomization of transform parameters.
    #   fixed     — `seed` used verbatim, every run identical.
    #   per_run   — fresh random seed on each invocation (default).
    #   per_file  — deterministic seed derived from the input path.
    #   divergent — (v0.3.3) fresh per-invocation base seed, AND each segment
    #               derives its own seed from sha256(plan_hash, idx, base_seed).
    #               Adjacent segments get distinct transform-param phases, so
    #               a temporal-aware detector can't lock onto run-level uniformity.
    seed_strategy: SeedStrategy = "per_run"
    # v1.3.0 Task 30 — opt out of the watermark/station-ID guardrail
    # for sources the operator has already audited (e.g. corpus of
    # owned material with a known broadcaster bug overlay that the
    # operator licenses).  Default False so first-time users hit the
    # gate.  Mirrors the CLI's --accept-watermark-risk at the profile
    # level; either path attests legitimate use.
    skip_watermark_check: bool = False


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
    # v1.0.1: SHA-256 of the encoded segment at the moment it was marked
    # ``done``. On resume, the orchestrator re-hashes ``out_path`` and
    # demotes the segment back to ``pending`` if the on-disk file is
    # missing, zero bytes, or has a different hash — catching truncated
    # or corrupted segments that previous resumes would silently keep.
    # Post-hoc field: NOT part of compute_plan_hash() — adding a hash
    # must not invalidate every existing resume cache.
    sha256: str | None = None


class QARegistrationDetail(BaseModel):
    """Bounded alignment evidence for one registered QA media domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    offset_sec: float | None = Field(default=None, ge=-30.0, le=30.0)
    drift_sec_per_hour: float | None = Field(default=None, ge=-3600.0, le=3600.0)
    compared_samples: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    note: str | None = None


class QARegistration(BaseModel):
    """Provenance and per-domain evidence for registered QA metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_mode: Literal["plan_transformed"]
    plan_hash: str
    run_seed: int
    video: QARegistrationDetail | None = None
    audio: QARegistrationDetail | None = None


QAStatus = Literal["passed", "failed", "not_verified"]


class QACorrectness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: QAStatus
    scope: Literal["plan_contract", "pair_contract"]
    failure_codes: list[str] = Field(default_factory=list)
    full_decode_status: QAStatus = "not_verified"
    note: str | None = None

    @model_validator(mode="after")
    def consistent_status(self) -> QACorrectness:
        if self.status == "passed" and (
            self.full_decode_status != "passed" or self.failure_codes
        ):
            raise ValueError("passed correctness requires full decode and no failure codes")
        if self.full_decode_status == "failed" and self.status != "failed":
            raise ValueError("failed decoding requires failed correctness")
        return self


class QAAudioLoudness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    stream_index: int = Field(ge=0)
    status: QAStatus
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    method: Literal["ffmpeg_loudnorm_full_decode"] = "ffmpeg_loudnorm_full_decode"
    note: str | None = None


class QALoudness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: QAStatus
    streams: list[QAAudioLoudness] = Field(default_factory=list)
    note: str | None = None


class QAQualityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    domain: Literal["raw", "registered"] = "raw"
    min_vmaf: float | None = Field(default=None, ge=0.0, le=100.0)
    min_ssim: float | None = Field(default=None, ge=-1.0, le=1.0)


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
    # v0.2 legacy chunked self-similarity heuristic and corpus matches.  The
    # compatibility field names are not calibrated external-system predictions.
    cid_predict_self: float | None = None
    weakest_chunk_sec: tuple[float, float] | None = None
    chunk_similarities: list[dict[str, float]] = Field(default_factory=list)
    corpus_matches: list[dict[str, float | str]] = Field(default_factory=list)
    # v0.8.0 R4 — SSCD (Self-Supervised Copy Detection) embedding similarity
    # between source and output, on a deterministic frame grid. Mean and
    # min are the headline KPIs (1.0 = identical, 0.0 = unrelated). The
    # per-frame list is kept for the HTML chart and downstream analysis.
    # Populated only when build_report(..., compute_sscd=True) and the
    # ``[ml]`` extra is installed. Flat fields rather than a nested model
    # so existing JSON tooling keeps reading the report shape unchanged.
    sscd_mean: float | None = None
    sscd_min: float | None = None
    sscd_per_frame: list[float] | None = None
    # RFC #12 — additive, plan-aware diagnostics. Existing raw metrics and
    # verdict semantics remain unchanged.
    vmaf_registered_mean: float | None = Field(default=None, ge=0.0, le=100.0)
    ssim_registered_mean: float | None = Field(default=None, ge=-1.0, le=1.0)
    sscd_registered_mean: float | None = Field(default=None, ge=-1.0, le=1.0)
    audio_fp_registered_hamming_per_frame: float | None = Field(
        default=None, ge=0.0, le=32.0,
    )
    registration: QARegistration | None = None
    correctness: QACorrectness | None = None
    loudness: QALoudness | None = None
    quality_policy: QAQualityPolicy | None = None
