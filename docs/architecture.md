# Architecture

Three layers, with the `Plan` (pydantic) as the contract between them.

```
+------------------------------------------------------+
| GUI (optional [gui]): PyQt6 — gui/app_pyqt.py         |
| CLI (typer):  cli/{app, cmd_run, cmd_probe,           |
|               cmd_preflight, cmd_qa, cmd_batch}.py    |
+----------------------------+-------------------------+
                             |  Plan + RunOptions
                             v
+------------------------------------------------------+
| core/  (pure Python, no Qt, no UI)                   |
|                                                      |
|  probe  → planner → preflight → segmenter → pipeline |
|     │        │                       │          │    |
|     v        v                       v          v    |
| encoder              checkpoint           runner     |
|                                                      |
|                       concat → metadata → qa         |
+------------------------------------------------------+
                             |
                             v
                  ffmpeg / ffprobe / fpcalc
```

## Modules

| Path | Role |
|------|------|
| `core/models.py` | All pydantic dataclasses: SourceMeta, Plan, Profile, Segment, QAReport |
| `core/probe.py` | `probe(path) -> SourceMeta` via single ffprobe call |
| `core/encoder.py` | `detect_encoders()` with real test-run + cache; `pick_encoder()` |
| `core/transforms/` | 9 transforms registered at import; see [filter_graph.md](./filter_graph.md) |
| `core/pipeline.py` | `FilterGraph.build()` + `build_video_segment_command` + `build_main_audio_command` |
| `core/runner.py` | subprocess wrapper with `-progress pipe:1`, RunEvent stream, CancelToken |
| `core/segmenter.py` | keyframe-aware split + per-segment process + concat demuxer |
| `core/checkpoint.py` | atomic `state.json` for resume |
| `core/metadata.py` | `-metadata` args + title templates |
| `core/preflight.py` | YouTube target matrix + HDR validation |
| `core/orchestrator.py` | `run_full(plan, options, on_event, cancel_token) -> RunSummary` |
| `core/profile_loader.py` | YAML → `Profile` with pydantic validation |
| `core/qa/` | hashes, phash, audio_fp (chromaprint), vmaf, ssim, report + HTML |

## Data flow for one input

```
input.mp4
  ├─ probe()              → SourceMeta (streams, HDR, chapters)
  ├─ load_profile()       → Profile (transforms, audio_tracks, keep_hdr…)
  ├─ pick_encoder()       → EncoderCandidate
  ├─ compute_plan_hash()  → Plan (frozen)
  ├─ preflight()          → list[PreflightFinding] (fail → stop)
  ├─ plan_segments()      → list[Segment] (keyframe-aligned)
  ├─ checkpoint init/resume
  ├─ for each pending segment:
  │     stream_copy_extract → seg_NNNN_src.mkv
  │     filter_complex      → seg_NNNN.mkv
  │     state.json mark done
  ├─ process_main_audio() ← runs on full source, cached via state.json
  ├─ concat_segments()    → concat demuxer + stream-copy mux of main audio
  └─ build_report()       → output.qa.json + output.qa.html
output.mp4
```

## Why a separate orchestrator

`core/orchestrator.run_full()` is the **single entry point** that CLI commands
(`cmd_run`, `cmd_batch`) and the GUI Worker all call. It receives an
`on_event` callback so any UI can stream `RunEvent`s without baking presentation
logic into the core. This fixes the architectural debt of the legacy AB
prototype, which embedded the entire pipeline inside a PyQt `QThread`.

## Why split-process-concat (not keyframe seek)

See specs/03-segmenter-resume-metadata-preflight.md §5. Summary:

- A single ffmpeg `-ss <T>` resume loses all progress on crash.
- Per-segment processing makes resume O(1) and gives clean MD5-stable outputs.
- Main audio is processed **outside** segmentation on the full source — loudnorm
  and pitch shift have transient behaviour at seams, so we never split audio.
