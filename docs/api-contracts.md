# API Contracts

This page is the authoritative reference for `yt-uniquifier`'s
**public, stable API surface** under the SemVer commitment in
[Versioning](versioning.md). It is bootstrapped in v1.0.0 R1; the
field-by-field stability table will be filled in by R2 alongside
the snapshot tests under `tests/contracts/`.

Until R2 ships, treat this page as the contract index: the
authoritative shape is whatever the pydantic models in
`src/yt_uniquifier/core/models.py` produce when serialised via
`Model.model_dump(mode="json")`.

## Surfaces covered by the SemVer commitment

| Surface | Source | Stability |
|---|---|---|
| `Plan` | `core/models.py::Plan` | **stable** |
| `Profile` | `core/models.py::Profile` (+ `extra=forbid`) | **stable** |
| `SourceMeta` | `core/models.py::SourceMeta` | **stable** |
| `EncoderCandidate` | `core/models.py::EncoderCandidate` | **stable** |
| `RunEvent` | `core/models.py::RunEvent` | **stable** |
| `RunOptions` | `core/orchestrator.py::RunOptions` | **stable** |
| `RunSummary` | `core/orchestrator.py::RunSummary` | **stable** |
| `QAReport` | `core/models.py::QAReport` | **stable** |
| `build_plan(...)`, `run_full(...)`, `compute_plan_hash(...)` | `core/orchestrator.py`, `core/pipeline.py` | **stable** |
| CLI subcommands (`run`, `batch`, `qa`, `calibrate`, `worker`, `corpus`, `queue`, `probe`, `preflight`, `profile`, `subtitles`, `telemetry`, `version`) | `cli/` | **stable** |
| Profile YAML schema (all 17 shipped transforms + global keys) | `src/yt_uniquifier/profiles/*.yaml`, `core/profile_loader.py` | **stable** |
| Plugin entry-point group `yt_uniquifier.transforms` | `core/transforms/__init__.py` (v0.8.0 R1) | **stable** |

## RunEvent kinds (v1.0.0 baseline)

A `RunEvent.kind` literal is part of the contract. Adding a new
`kind` is MINOR; removing or changing the payload schema of an
existing one is MAJOR.

The full list will be enumerated in R2 from a grep of
`RunEvent(kind=` across `src/`. The set as of the v1.0.0 freeze
includes (non-exhaustive, R2 will lock it):
`probe`, `preflight`, `plan`, `segment_start`, `segment_done`,
`audio_start`, `audio_done`, `concat`, `qa`, `completed`,
`cancelled`, `failed`, `target_vmaf`, `target_vmaf_failed`,
`paused`, `resumed`, and `phash_sample`.

## Out of scope

- Everything under `core/_internal/`, `gui/`, `cli/_*` is
  **internal** and may change at any release.
- `gui/` is **stable in user-visible behaviour** (shortcuts,
  screen layout, accessible names) but the Python module API of
  `gui/screens/*` and `gui/workers/*` is internal.
- The `[web]` HTTP API is currently **experimental** — pin
  `yt-uniquifier==1.0.*` if you depend on it. R2 will decide
  whether to promote it to stable.

## How to use this page

If you are writing code that imports from `yt_uniquifier.core`,
check the table above first. If the surface you want is **stable**,
the SemVer contract applies. If it is **experimental**, pin to
PATCH. If it is **internal** or not listed, open an
[RFC](versioning.md#rfc-process-for-breaking-changes) to request
promotion or be ready for it to change.

## See also

- [Versioning & compatibility](versioning.md) — SemVer commitment
  and RFC process.
- [`SECURITY.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/SECURITY.md) — disclosure policy.
- [`CHANGELOG.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/CHANGELOG.md) — every accepted breaking change is recorded here.
