# Architecture

Three layers, with the `Plan` (pydantic) as the contract between them.

```
+---------------------------------------------------------+
| GUI (optional [gui]): PyQt6 — gui/app_pyqt.py           |
| CLI (typer): cli/{app, cmd_run, cmd_probe,              |
|              cmd_preflight, cmd_qa, cmd_batch,          |
|              cmd_calibrate, cmd_corpus,                 |
|              cmd_queue, cmd_worker}.py                  |
+----------------------------+----------------------------+
                             |  Plan + RunOptions
                             v
+---------------------------------------------------------+
| core/  (pure Python, no Qt, no UI)                      |
|                                                         |
|  probe  → planner → preflight → segmenter → pipeline    |
|     │        │                       │          │       |
|     v        v                       v          v       |
| encoder              checkpoint           runner        |
|                                                         |
|  seed_resolver → divergent per-segment seeds            |
|  calibration   → bisect intensity vs cid_predict        |
|  qa            → phash + audio_fp + vmaf + ssim         |
|                + cid_predict + corpus + chunked QA      |
|  queue / worker → shared-FS distributed batch           |
|                                                         |
|                       concat → metadata → qa            |
+---------------------------------------------------------+
                             |
                             v
                 ffmpeg / ffprobe / fpcalc
```

## Modules

| Path | Role |
|------|------|
| `core/models.py` | All pydantic dataclasses: SourceMeta, Plan, Profile, Segment, QAReport |
| `core/probe.py` | `probe(path) -> SourceMeta` via single ffprobe call |
| `core/encoder.py` | `detect_encoders()` with real test-run + cache; `pick_encoder()`; per-candidate `max_parallel` cap |
| `core/transforms/` | **18 transforms** registered at import; see [filter_graph.md](./filter_graph.md) |
| `core/transforms/hdr_wrap.py` | zscale linear-light roundtrip for color transforms over HDR |
| `core/pipeline.py` | `FilterGraph.build()` + `build_video_segment_command` + `build_main_audio_command` + `compute_plan_hash` |
| `core/runner.py` | subprocess wrapper with `-progress pipe:1`, RunEvent stream, CancelToken |
| `core/segmenter.py` | keyframe-aware split + per-segment process (parallel where safe) + concat demuxer |
| `core/checkpoint.py` | atomic `state.json` for resume |
| `core/seed_resolver.py` | `resolve_run_seed(profile, source)` + `derive_segment_seed(plan_hash, idx, run_seed)` for the `divergent` strategy |
| `core/metadata.py` | `-metadata` args + title templates |
| `core/preflight.py` | YouTube target matrix + HDR validation |
| `core/orchestrator.py` | `run_full(plan, options, on_event, cancel_token) -> RunSummary` |
| `core/profile_loader.py` | YAML → `Profile` with pydantic validation |
| `core/qa/` | hashes, phash, audio_fp (similarity + Hamming distance), vmaf, ssim, cid_predict, corpus, quality (fallback chain), report + HTML |
| `core/calibration/` | `intensity.scale_profile` (multiplicative scaling around identity) + `loop.calibrate` (bisect against `cid_predict`) |
| `core/queue/leasing.py` | Atomic POSIX-rename file queue (`pending/`, `in_progress/`, `done/`, `failed/`) with heartbeat + reaper |
| `cli/cmd_worker.py` | Long-running queue drainer that calls `orchestrator.run_full` per leased file |

## Data flow for one input

```
input.mp4
  ├─ probe()              → SourceMeta (streams, HDR, chapters)
  ├─ load_profile()       → Profile (transforms, audio_tracks, keep_hdr, seed_strategy…)
  ├─ pick_encoder()       → EncoderCandidate (with max_parallel cap)
  ├─ compute_plan_hash()  → Plan (frozen)
  ├─ resolve_run_seed()   → run_seed (per_run / per_file / fixed / divergent base)
  ├─ preflight()          → list[PreflightFinding] (fail → stop)
  ├─ plan_segments()      → list[Segment] (keyframe-aligned)
  ├─ checkpoint init/resume
  ├─ process_video_segments_parallel(workers=N, cap=encoder.max_parallel):
  │     for each pending segment:
  │       seg_plan = plan_for_segment(plan, idx)   # divergent: per-seg seed
  │       stream_copy_extract → seg_NNNN_src.mkv
  │       filter_complex      → seg_NNNN.mkv
  │       state.json mark done
  ├─ process_main_audio() ← runs on full source; two-pass loudnorm cached in state.json
  ├─ concat_segments()    → concat demuxer + stream-copy mux of main audio
  └─ build_report()       → output.qa.json + output.qa.html
output.mp4
```

## Distributed flow

```
Worker A (machine 1) ──┐
                       │
Worker B (machine 2) ──┼── pending/   ◄── queue add
                       │   ▲
Worker C (machine 3) ──┘   │ atomic rename
                           │
                       in_progress/<host>.<file>  + <host>.alive (heartbeat)
                           │ (success)
                           v
                       done/                          ◄── prune by mtime
                           │ (failure)
                           v
                       failed/<file>.err
```

Each worker calls `orchestrator.run_full` per leased file. No shared
state beyond the queue directories themselves; see [distributed.md](./distributed.md).

## Why a separate orchestrator

`core/orchestrator.run_full()` is the **single entry point** that CLI commands
(`cmd_run`, `cmd_batch`, `cmd_worker`) and the GUI Worker all call. It receives
an `on_event` callback so any UI can stream `RunEvent`s without baking
presentation logic into the core. This fixes the architectural debt of the
legacy AB prototype, which embedded the entire pipeline inside a PyQt
`QThread`.

## Why split-process-concat (not keyframe seek)

See `specs/03-segmenter-resume-metadata-preflight.md`. Summary:

- A single ffmpeg `-ss <T>` resume loses all progress on crash.
- Per-segment processing makes resume O(1) and gives clean MD5-stable outputs
  for the same `(plan_hash, run_seed)`.
- Main audio is processed **outside** segmentation on the full source —
  loudnorm and pitch shift have transient behaviour at seams, so we never
  split audio.

## Seed strategy

`Profile.seed_strategy` controls per-run variability:

- `fixed`     — `profile.seed` verbatim; every run identical.
- `per_run`   — fresh random seed per invocation (legacy default).
- `per_file`  — deterministic hash of source path; same input → same seed.
- `divergent` — fresh per-run base seed AND a per-segment seed derived via
                `sha256(plan_hash, segment_idx, run_seed)`. Adjacent segments
                get distinct transform-param phases. Default for v0.3.3
                CID-aware profiles.

See [seed_strategy.md](./seed_strategy.md) for use cases and reproducibility
implications.
