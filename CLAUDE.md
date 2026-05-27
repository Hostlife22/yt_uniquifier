# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`yt-uniquifier` — a Python 3.11+ CLI/GUI re-encoder that wraps `ffmpeg` and applies controlled micro-transforms (crop+rescale, color jitter, noise, rotation, frame-blend, pitch/tempo, EQ, EBU R128 loudnorm) to owned/licensed video. The scope is legitimate use only (re-uploading your own content, fair-use derivatives). The README and CLI help text explicitly disclaim Content-ID evasion — preserve that framing in user-facing strings and docs.

## Common commands

```bash
pip install -e ".[dev]"          # dev install (CLI only)
pip install -e ".[dev,gui]"      # + PyQt6 GUI
pip install -e ".[dev,qa]"       # + chromaprint bindings (audio fingerprint)

ruff check .                      # lint (line-length 100, target py311)
mypy src/yt_uniquifier             # strict typing (mypy strict = true)
pytest -q                          # run full suite
pytest tests/unit/test_pipeline_graph.py -q   # single file
pytest -k "label_allocator" -q                # single test by keyword
pytest -m smoke -q                            # only smoke tests
pytest -m integration -q                      # tests that invoke real ffmpeg
```

Custom pytest markers (declared in `pyproject.toml`): `integration` (real ffmpeg), `smoke` (CI sanity).

CLI entry points (after `pip install -e .`):

- `yt-uniq` — `yt_uniquifier.cli.app:app` (subcommands: `version`, `probe`, `preflight`, `run`, `qa`, `batch`)
- `yt-uniq-gui` — `yt_uniquifier.gui.app_pyqt:main` (requires `[gui]` extra)

Requires `ffmpeg`/`ffprobe` on PATH. Optional binaries — graceful skip if absent: `fpcalc` (chromaprint), ffmpeg with `libvmaf`.

## Architecture

Three layers separated by the `Plan` (pydantic) contract:

1. **`cli/`** (typer) and **`gui/`** (PyQt6) — thin shells. They build a `Plan` + `RunOptions` and hand them to the orchestrator. No business logic here.
2. **`core/`** — pure Python, no Qt, no UI dependencies. This is where the work happens.
3. **External binaries** — `ffmpeg` / `ffprobe` / `fpcalc` invoked via `subprocess`.

Pipeline flow for one input:

```
probe()  → SourceMeta
profile  → Profile (YAML, validated)
pick_encoder() → EncoderCandidate (NVENC/QSV/AMF/VideoToolbox/x264/x265)
build_plan() → Plan (frozen, hashed for resume keying)
preflight() → list[PreflightFinding]   # fails block run unless overridden
plan_segments() → keyframe-aligned segments
for each pending segment:               # resumable via state.json
  stream_copy_extract → filter_complex re-encode → mark done
process_main_audio()                    # runs once on full source; loudnorm two-pass cached
concat_segments()                       # concat demuxer + stream-copy mux
build_report()                          # → <out>.qa.json + <out>.qa.html
```

Key invariants:

- **One `ffmpeg -filter_complex` per segment.** No bgr24 round-trip through Python stdin. `core/pipeline.py::FilterGraph.build` composes per-transform fragments via `LabelAllocator`.
- **Resume = split-process-concat, not keyframe seek.** A crash during a multi-hour run resumes at segment boundary granularity (see `core/segmenter.py`, `core/checkpoint.py`). Plan hash keys the resume cache — changing the profile invalidates it.
- **Audio is never split across segments.** `process_main_audio` runs on the full source so loudnorm and pitch transients don't break at seams. The two-pass loudnorm measurement is cached in `state.json`.
- **Single orchestrator entry point.** `core/orchestrator.py::run_full(plan, options, on_event, cancel_token) -> RunSummary` is what CLI commands and the GUI Worker both call. It takes an `on_event` callback so UIs can stream `RunEvent`s without coupling. The legacy AB prototype embedded the pipeline inside a `QThread` — don't reintroduce that.
- **Transforms self-register on import.** `core/transforms/__init__.py` imports every submodule; each calls `register(TransformSpec(...))`. To add a transform: create a new submodule, register, add to `__init__.py`, write a snapshot test in `tests/unit/test_transforms.py` against the generated `filter_complex` string.
- **Encoder detection is real, cached.** `core/encoder.py::detect_encoders` runs each candidate against a `lavfi` null source and caches results. Tests must use the `isolated_cache` fixture (redirects `CACHE_PATH` to `tmp_path`) so they don't pollute or depend on the user's cache.
- **Even-dimensions guard at the tail of the video chain.** `scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p` — `libx264` rejects odd dims after micro-crop.
- **`video.blend_b` is the only multi-input transform.** It returns `extra_inputs` and uses the `__B__` token rewritten to `[1:v]` after `-i B.mp4` is appended.

## Models and contracts

`core/models.py` holds every pydantic dataclass: `SourceMeta`, `Profile`, `Plan`, `Segment`, `EncoderCandidate`, `QAReport`, `RunEvent`. The `Plan` is JSON-serializable on purpose — it crosses thread/process boundaries and is hashed for resume.

Profiles (`src/yt_uniquifier/profiles/*.yaml`) are loaded via `core/profile_loader.py` with `extra=forbid`. Shipped: `soft.yaml`, `medium.yaml`, `aggressive.yaml`, `legacy_ab.yaml`. See `docs/profiles.md` for the schema.

## Tests

- `tests/conftest.py` provides `tiny_clip` (session-scoped, generated via `testsrc2`+sine — no binary fixtures in the repo) and `isolated_cache` (tempdir encoder cache). Use the `needs_ffmpeg` skip marker from `conftest` for tests requiring real ffmpeg.
- `tests/unit/` — fast, no subprocess: transforms tested via snapshot of the generated `filter_complex` string; pipeline graph via the same; checkpoint, models, preflight via pure-Python.
- `tests/integration/` — invokes real ffmpeg on `tiny_clip`. Mark with `@pytest.mark.integration`.
- `tests/smoke/` — minimal `--version` and GUI import checks.

When adding a transform: snapshot test the `filter_complex` fragment. Do not assert on raw ffmpeg output bytes.

## CI

`.github/workflows/ci.yml` runs `ruff check .` + `pytest -q` on `{ubuntu-latest, macos-latest} × {3.11, 3.12}`. ffmpeg is installed via apt/brew. Keep the suite green on both OSes — macOS adds VideoToolbox to encoder detection.

## Specs

`specs/` is the phased implementation plan — currently `00-bootstrap` through `16-temporal-jitter-and-divergence`, plus version roadmaps (`v0.2-plan.md`, `v0.3-plan.md`, `v0.3.2-3-plan.md`). They are the spec of record for module signatures and acceptance criteria — consult the matching phase before significant changes to a module. Phases 06+ extend the v0.1 baseline with HDR pipeline, audio CID-resistance, fingerprint-aware QA, calibration loop, parallel/distributed batch, and per-segment seed divergence; treat them as additive, not retroactive.

## Docs

- `docs/architecture.md` — layer diagram and data flow (authoritative)
- `docs/profiles.md` — YAML profile schema and transform reference
- `docs/filter_graph.md` — how transforms compose into `-filter_complex`
- `docs/youtube_targets.md` — preflight target matrix
- `docs/seed_strategy.md` — deterministic per-segment/per-transform seed derivation (load-bearing for reproducibility and divergence guarantees)
- `docs/qa_report.md` — schema and KPIs for `<out>.qa.json` / `.qa.html`
- `docs/calibrate.md` — calibration loop (auto-tunes profile params against fingerprint deltas)
- `docs/distributed.md` — multi-host batch coordination
