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
|  seed_resolver  → divergent per-segment seeds           |
|  audio_windows  → per-window audio seeds (divergent)    |
|  calibration    → bisect intensity vs cid_predict       |
|  qa             → phash + audio_fp + vmaf + ssim        |
|                 + cid_predict + corpus + chunked QA     |
|  queue / worker → shared-FS distributed batch           |
|  sanitizer      → optional libx264 bitstream rewrap     |
|  errors         → typed exception hierarchy             |
|                                                         |
|                concat → (sanitize?) → metadata → qa     |
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
| `core/transforms/` | **19 transforms** registered at import (10 video, 9 audio — `video_geom.py` registers `crop_resize`, `rotate`, `mirror`); see [filter_graph.md](./filter_graph.md) |
| `core/transforms/hdr_wrap.py` | zscale linear-light roundtrip for color transforms over HDR |
| `core/pipeline.py` | `FilterGraph.build()` + `build_video_segment_command` + `build_main_audio_command` + `compute_plan_hash` |
| `core/runner.py` | subprocess wrapper with `-progress pipe:1`, RunEvent stream, CancelToken |
| `core/segmenter.py` | keyframe-aware split + per-segment process (parallel where safe) + concat demuxer |
| `core/checkpoint.py` | atomic `state.json` for resume (thread-safe `RLock` + fsync + `os.replace`) |
| `core/seed_resolver.py` | `resolve_run_seed(profile, source)` + `derive_segment_seed(plan_hash, idx, run_seed)` for the `divergent` strategy |
| `core/audio_windows.py` | `~60 s` audio windowing for `divergent` strategy — per-window seed via `derive_segment_seed(plan_hash, window_idx + 1_000_000, run_seed)` with `0.1 s` crossfade; loudnorm stays global |
| `core/metadata.py` | `-metadata` args + title templates |
| `core/preflight.py` | YouTube target matrix + HDR validation + dry-run filter probes (`_ffmpeg_filter_works`) for `rubberband`, `zscale`, and tonemap dependencies |
| `core/sanitizer.py` | Opt-in second-pass libx264 re-encode (`yt-uniq run --sanitize-bitstream`) to normalize file-level encoder signature; no-op on libx264 source, refuses HEVC/HDR-keep |
| `core/orchestrator.py` | `run_full(plan, options, on_event, cancel_token) -> RunSummary` |
| `core/profile_loader.py` | YAML → `Profile` with pydantic validation (`extra=forbid`) |
| `core/errors.py` | Typed exception hierarchy: `YtUniquifierError`, `ProbeError`, `EncoderError`, `FfmpegNotFoundError`, `PipelineError`, `CheckpointError`, `PreflightFailure` |
| `core/utils/ffmpeg_paths.py` | `ffmpeg_bin()` / `ffprobe_bin()` resolver — single source of truth for binary lookup |
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
  │   (divergent strategy: audio_windows.plan_windows → per-window seed + 0.1 s crossfade)
  ├─ concat_segments()    → concat demuxer + stream-copy mux of main audio
  ├─ sanitize_bitstream() → optional libx264 rewrap (opt-in via --sanitize-bitstream)
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

## RunEvent contract

`core/runner.RunEvent` is a frozen dataclass with a `kind: EventKind`
discriminator and a free-form `payload: dict[str, object]`. The
`on_event` callback is the only path information leaves the core layer,
so its surface area is part of the public contract — adding a new
`kind` is a backwards-compatible extension; **renaming** one is a
breaking change.

| `kind`               | Producer                              | Payload keys (load-bearing) |
|---|---|---|
| `progress`           | `runner._run_once` (ffmpeg `-progress`) | `out_time_us` *or* `out_time_ms`, `frame`, `phase`, `segment` |
| `log`                | orchestrator / segmenter / runner watcher | `phase` (e.g. `preflight`, `plan`, `segment`, `main_audio`, `paused`, `resumed`, `concat`, `sanitize`, `cleanup`), `message` |
| `error`              | any layer                              | `phase`, `message`, `returncode`, `tail`, `findings` |
| `done`               | `runner.run` (success exit)            | `duration_sec`, `output` |
| `divergence_sample`  | `orchestrator._maybe_emit_divergence` (v0.7 R4 / F2) | `segment`, `phash_similarity` (per-segment), `running_phash` (EMA, α=0.25), `frames_sampled` |

Phase invariants used by GUI consumers:

- `phase=segment` carries `segment: int` so progress aggregators can
  map ffmpeg's stream-local `out_time_us` back to the correct overall
  bucket. The orchestrator's `_wrap` closure in `segmenter.py` injects
  it; downstream consumers must not assume `segment` is set on every
  event.
- `phase=main_audio` is dedicated to the loudnorm + audio re-encode
  pass and drives the secondary "Audio" progress bar in the GUI; the
  main bar stays pinned to the video-segments-derived value.
- `phase=paused` / `phase=resumed` fire exactly once per state
  transition (v0.7 R6 / F5) — the runner watcher tracks the applied
  SIGSTOP state and de-duplicates repeated token flips.

## Cancel + Pause plumbing (v0.7 R6 / F5)

The core layer offers two cooperative cancellation primitives, both
`threading.Event`-backed so a write from any thread (GUI button →
QThread, signal handler → main loop) is observed correctly on the
worker thread:

```
                              ┌──────────────────────────┐
            GUI Run screen ──►│ RunWorker (QThread)      │
            (Esc / Space)     │                          │
                              │  cancel_token: CancelToken
                              │  pause_token:  PauseToken
                              └────────────┬─────────────┘
                                           │ run_full(plan, options,
                                           │   on_event=…,
                                           │   cancel_token=…,
                                           │   pause_token=…)
                                           v
                              ┌──────────────────────────┐
                              │ orchestrator             │
                              │  ├─ pause observer thread│
                              │  │   writes paused_at    │
                              │  │   to state.json on    │
                              │  │   pause/resume        │
                              │  │   enforces 24h        │
                              │  │   auto-cancel         │
                              │  │                       │
                              │  └─ process_video_segments_parallel
                              │     ↓ pause_token forwarded
                              └──────────────────────────┘
                                           │
                                           v
                              ┌──────────────────────────┐
                              │ runner.run / _run_once   │
                              │  watcher thread:         │
                              │    cancel → SIGTERM      │
                              │    pause  → SIGSTOP via  │
                              │      process_control     │
                              │    resume → SIGCONT      │
                              └────────────┬─────────────┘
                                           v
                                       ffmpeg
```

`PauseToken` is **idempotent** (repeated `pause()` / `resume()` calls
are no-ops), records the wall-clock pause timestamp for
state.json persistence, and exposes `should_auto_cancel()` —
the orchestrator's daemon `_start_pause_observer` polls it once per
second and fires `cancel_token.cancel()` if the pause exceeds
`PauseToken.AUTO_CANCEL_SEC` (24 h by default).

`core/process_control.py` is the cross-OS abstraction: POSIX uses
stdlib `os.kill(pid, SIGSTOP|SIGCONT)`; Windows uses a lazy `psutil`
import (no hard dep — pause becomes a no-op with a warning if psutil
is absent). Both walk the process tree (psutil children, or `/proc`
fallback on Linux) so descendant ffmpeg helper processes are
suspended too. All operations are best-effort and never raise — a
failed `os.kill` is logged at WARN and the return value reports the
ack count.
