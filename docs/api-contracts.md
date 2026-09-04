# API Contracts

This page is the authoritative reference for `yt-uniquifier`'s
**public, stable API surface** under the SemVer commitment in
[Versioning](versioning.md). Every entry below is enforced by a
snapshot test under `tests/contracts/`; a contract drift fails CI
with a pointer to `tools/regen_contract_goldens.py`.

## Stability labels

- **`stable`** — covered by the SemVer commitment. MAJOR bump
  required to remove or break; MINOR may add fields.
- **`experimental`** — covered, but MAY be removed in a MINOR with
  a `DeprecationWarning`. Pin `==1.3.*` if you depend on it.
- **`internal`** — not covered. Default for anything not listed.

## How to import

The supported entry point is the `yt_uniquifier.core` barrel:

```python
from yt_uniquifier.core import (
    Plan, Profile, RunOptions, RunSummary, RunEvent,
    build_plan, run_full, compute_plan_hash,
)
```

Importing from submodules (`from yt_uniquifier.core.orchestrator
import ...`) still works but is **not** covered by SemVer — those
paths may be reorganised internally between MINOR releases.

## Locked surfaces

### Pydantic models (`stable`)

Every model below has its full JSON schema locked by
`tests/contracts/test_pydantic_schemas_stable.py`. Goldens live in
`tests/fixtures/contracts/schemas/`.

| Model | Source | Notes |
|---|---|---|
| `HDRInfo` | `core/models.py` | `frozen=True`. Color characteristics plus optional static/dynamic HDR metadata. |
| `VideoStream` | `core/models.py` | `frozen=True`. One video stream from `ffprobe`. |
| `AudioStream` | `core/models.py` | `frozen=True`. v1.4 adds optional `title` and `dispositions`. |
| `SubtitleStream` | `core/models.py` | `frozen=True`. v1.4 adds optional `title`, `is_default`, and `dispositions`. |
| `Chapter` | `core/models.py` | `frozen=True`. |
| `SourceMeta` | `core/models.py` | `frozen=True`. Stable serialized A/V/subtitle/chapter probe result. Auxiliary attachment/data/cover-art topology is intentionally private/internal so this corrective change does not alter the v1 schema. |
| `EncoderCandidate` | `core/models.py` | `frozen=True`. `max_parallel ∈ [1, 64]`; includes AV1 software vendors `svtav1` and `libaom`. |
| `TransformConfig` | `core/models.py` | `extra="forbid"`. `params: dict[str, object]` — the parameter dict is shape-checked at the transform's own `*Params` model, not here. |
| `SegmentationConfig` | `core/models.py` | `extra="forbid"`. v0.8.0 added `mode="scene"` opt-in. |
| `Profile` | `core/models.py` | `extra="forbid"`. The user-facing YAML schema; v1.3 adds `skip_watermark_check`. |
| `Plan` | `core/models.py` | `frozen=True`. Carries `plan_hash` (resume key) + `run_seed` (NOT part of the hash). |
| `QAReport` | `core/models.py` | `frozen=True`. All ML/fingerprint metrics are `Optional`; v1.5 adds nullable plan-registered metric fields and nested bounded registration provenance while preserving all raw fields. |

### Dataclasses (`stable`)

Field shapes (name + type repr + has-default) are locked by
`tests/contracts/test_runoptions_dataclass_stable.py` and
`tests/contracts/test_runevent_kinds_stable.py`. Default *values*
are explicitly NOT locked — a behaviour-only default tweak
(`target_segment_sec=600` → `900`) is allowed within PATCH.

| Dataclass | Source | Locked fields |
|---|---|---|
| `RunEvent` | `core/runner.py` | `kind`, `payload` |
| `RunResult` | `core/runner.py` | `returncode`, `duration_sec`, `output_path` |
| `RunOptions` | `core/orchestrator.py` | `work_dir`, `output`, `encoder_override`, `title_template`, `target_segment_sec`, `keep_segments`, `enforce_preflight`, `force_new_variant`, `workers`, `sanitize_bitstream`, `sample_phash`, `notifications`, `telemetry`, `run_id`, `accept_watermark_risk`, `audit_log_path`, `audit_principal` |
| `RunSummary` | `core/orchestrator.py` | `output`, `plan`, `segments_done`, `preflight_findings` |

### RunEvent kinds (`stable`)

`tests/contracts/test_runevent_kinds_stable.py` enumerates the
exact tuple. As of v1.0.0:

| `kind` | Carried by | Payload contract |
|---|---|---|
| `progress` | `runner.run_ffmpeg` per `-progress pipe:1` block | dict mirroring ffmpeg progress fields (`out_time_ms`, `fps`, `speed`, ...) |
| `log` | orchestrator / segmenter / runner | `phase: str` and free-form context. Treat payload keys as **experimental** — additions are MINOR. |
| `done` | `runner.run_ffmpeg`, `orchestrator.run_full` | `duration_sec` or `output` |
| `error` | any phase | `reason`, `phase`, optional `returncode`, `tail` |
| `divergence_sample` | `orchestrator._maybe_emit_divergence` (v0.7 R4) | `segment_idx`, `phash_similarity`, `ema` |
| `target_vmaf` | `segmenter._encode_once` per attempt (v0.8 R5) | `segment`, `vmaf`, `crf`, `attempt`, `target` |
| `target_vmaf_failed` | `segmenter._encode_once` after exhaustion | as above, plus `best_attempt` |

Adding a new kind is **MINOR**; consumers MUST tolerate unknown
kinds (e.g. via a default `if-elif-else` branch that logs and
drops). Removing or renaming a kind is **MAJOR** and requires an
RFC.

### Profile YAML schema (`stable`)

Every shipped profile's loaded form is locked by
`tests/contracts/test_shipped_profiles_stable.py` (goldens under
`tests/fixtures/contracts/profiles/`). The set itself is locked by
`shipped_profiles.json`. As of v1.3.0:

| Profile | Container | Codec | LUFS | Notes |
|---|---|---|---|---|
| `soft` | mp4 | h264 | -14 | Conservative authorized derivative; natural-corpus band pending. |
| `medium` | mp4 | h264 | -14 | Moderate processing; natural-corpus band pending. |
| `medium_hdr` | mp4 | hevc | -14 | HDR-keep path via `zscale` linear wrap. |
| `aggressive` | mp4 | h264 | -14 | Experimental high-change stack; operator review required. |
| `cid_aware` | mp4 | h264 | -14 | Legacy experimental compatibility profile; no external-system prediction. |
| `cid_aware_hdr_to_sdr` | mp4 | h264 | -14 | Experimental HDR → SDR derivative path. |
| `cid_aggressive` | mp4 | h264 | -14 | Legacy maximum-change stack; not quality-first. |
| `youtube_4k` | mp4 | h264 | -14 | YouTube 4K target geometry + bitrate. |
| `youtube_1080p` | mp4 | h264 | -14 | YouTube 1080p target. |
| `youtube_av1` | mp4 | av1 | -14 | YouTube 1080p AV1 target. |
| `youtube_4k_av1` | mp4 | av1 | -14 | YouTube 4K AV1 target. |
| `youtube_shorts` | mp4 | h264 | -14 | 9:16 vertical, ≤60 s expected. |
| `tiktok_vertical` | mp4 | h264 | -14 | 9:16 vertical, TikTok loudness. |
| `instagram_reels` | mp4 | h264 | -14 | 9:16 vertical. |
| `instagram_square` | mp4 | h264 | -14 | 1:1 square. |
| `linkedin_square` | mp4 | h264 | -14 | 1:1 square, LinkedIn loudness. |

Adding a profile is MINOR; removing one is MAJOR (RFC).

### Public Python surface (`stable`)

`yt_uniquifier.__all__` and `yt_uniquifier.core.__all__` are locked
by `tests/contracts/test_public_surface_stable.py`. Adding a name
is MINOR; removing one is MAJOR.

### CLI subcommands (`stable`)

Every `yt-uniq` subcommand is part of the contract. Adding a
subcommand or flag is MINOR; removing or renaming is MAJOR.

| Subcommand | Source |
|---|---|
| `version` | `cli/app.py` |
| `probe` | `cli/cmd_probe.py` |
| `preflight` | `cli/cmd_preflight.py` |
| `run` | `cli/cmd_run.py` |
| `batch` | `cli/cmd_batch.py` |
| `qa` | `cli/cmd_qa.py` |
| `calibrate` | `cli/cmd_calibrate.py` |
| `worker` | `cli/cmd_worker.py` |
| `corpus` | `cli/cmd_corpus.py` |
| `queue` | `cli/cmd_queue.py` |
| `profile` | `cli/cmd_profile.py` (v0.9 R1) |
| `subtitles` | `cli/cmd_subtitles.py` (v0.9 R2) |
| `telemetry` | `cli/cmd_telemetry.py` (v0.9 R3) |
| `update` | `cli/cmd_update.py` (v1.3 Task 33) |

### Plugin entry-point group (`stable`)

`yt_uniquifier.transforms` (v0.8.0 R1) is the discovery point for
third-party transforms. The protocol that registered objects must
satisfy is `core.transforms.base.TransformSpec` — fields locked by
the pydantic schema test above.

## Surfaces marked `experimental`

Pin `yt-uniquifier==1.3.*` if you depend on any of these.

| Surface | Reason |
|---|---|
| `[web]` HTTP API (`src/yt_uniquifier/web/routes/*`) | v0.9 R4 ships as v1; routes may grow before promotion. |
| `RunEvent` payload key set (the dict itself is stable; individual keys are not) | Additive growth between MINOR releases is expected. |
| `core.telemetry.TelemetryConfig` | v0.9 R3 ships consent UX; future MINORs may add fields. |
| `core.notifications.NotificationConfig` | Provider auto-detect may evolve. |
| GUI `gui/screens/*` and `gui/workers/*` Python API | The screens / workers are internal; their *behaviour* (keyboard shortcuts, accessible names, screen layout) is the user-visible contract. |

## Out of scope (`internal`)

Anything not listed above. Specifically:

- Modules with a leading underscore (`core/_internal/`, `_*`).
- The `cli/_*` and `gui/_*` helper modules.
- Any utility under `core/utils/`.
- Test fixtures and dev tools (`tools/*`).

If you depend on something internal, open an
[RFC](versioning.md#rfc-process-for-breaking-changes) to request
promotion to `stable` or `experimental`.

## Snapshot tests reference

| Test file | What it locks | Golden directory |
|---|---|---|
| `test_pydantic_schemas_stable.py` | Each model's `model_json_schema()` | `tests/fixtures/contracts/schemas/` |
| `test_shipped_profiles_stable.py` | Each YAML's `model_dump(mode="json")` + the set of shipped profile names | `tests/fixtures/contracts/profiles/` + `shipped_profiles.json` |
| `test_runevent_kinds_stable.py` | `EventKind` literal members + `RunEvent` dataclass shape | `tests/fixtures/contracts/runevent_*.json` |
| `test_runoptions_dataclass_stable.py` | `RunOptions`, `RunSummary`, `RunResult` field shapes | `tests/fixtures/contracts/dataclasses/` |
| `test_public_surface_stable.py` | `yt_uniquifier.__all__` and `yt_uniquifier.core.__all__` | `tests/fixtures/contracts/public_surface/` |

## See also

- [Versioning & compatibility](versioning.md) — the SemVer
  commitment and the RFC process.
- [`SECURITY.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/SECURITY.md) — disclosure policy.
- [`CHANGELOG.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/CHANGELOG.md) — every accepted contract change is recorded here.
