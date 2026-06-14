# Plan: yt-uniquifier v0.8.0 — ML-grade QA + Plugin system

**Source plan**: `.claude/plans/yt-uniquifier-best-in-class.plan.md` § v0.8.0
**Selected milestone**: epic v0.8.0 (4–5 weeks in master plan; this plan slices it into 7 atomic rounds)
**Complexity**: **Large**
**Prereqs**: v0.7.0 shipped (commit `a5bbf07`), main green on 3-OS × 2-Py CI matrix.

---

## Summary

Six v0.8.0 deliverables from the master plan, scoped into 7 atomic, ship-shaped rounds. The ordering is foundation-first (plugin system → storage → segmentation → metric → feedback loop → calibrate wiring → docs). Each round is one focused commit that keeps `make check` green and is independently revertable.

The release introduces **two new optional extras**: `[ml]` (torch + SSCD model, ~250 MB lazy-downloaded) and `[scene]` (PySceneDetect). Core install stays slim. No new always-on dependencies.

---

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Optional extras | `pyproject.toml` `[project.optional-dependencies] qa = ["pyacoustid~=1.3"]` | Add `ml`, `scene` next to existing `gui`, `qa`, `gui-charts`. Module-level imports inside try/except, deferred to first call. |
| Transform registration | `core/transforms/__init__.py:1-22` + `transforms/base.py::register` | `TransformSpec(...)` registered at import; v0.8 wraps each import in `try/except + log.warning` and adds `entry_points("yt_uniquifier.transforms")` discovery after built-ins. |
| Profile schema extension | `core/models.py::Profile` (pydantic v2, `extra=forbid`) | Add `segmentation: SegmentationConfig` and `encoder.target_vmaf: float | None` as new validated sub-models. Default `mode="keyframe"` keeps existing profiles working. |
| Subprocess QA metric | `core/qa/vmaf.py` (libvmaf via ffmpeg + structured parse) | SSCD computation: deterministic frame grid via `ffmpeg -vf select=eq(n,...)` → PNG → torchscript model → cosine. Lazy import of torch. |
| Corpus storage | `core/qa/corpus.py` (JSON `index.json`, threading.Lock, atomic replace) | SQLite store keeps identical public API (`add_entry`, `iter_entries`, `lookup_by_id`, `purge`) — drop-in replaceable. Migration on first open. |
| Cancel + cancel-respecting loops | `core/qa/report.py:107` `_check_cancel(phase)` + `core/calibration/loop.py` accepts `cancel_token` | F11 feedback loop and SSCD compute both accept `CancelToken` and check between heavy ops. |
| GUI optional features | `gui/screens/settings.py` notification groupbox (v0.7 R5) | SSCD enable toggle and corpus migration button live as additive Settings groupboxes; absence of `[ml]`/`[scene]` extras shows a one-line install hint, never crashes. |

---

## Files to Change (high-level — round detail below)

| Area | Files | Action |
|---|---|---|
| Packaging | `pyproject.toml` | UPDATE — add `[ml]`, `[scene]` extras, `entry_points` group declaration |
| Plugin discovery | `core/transforms/__init__.py`, `core/transforms/base.py` | UPDATE — try/except per import, `_discover_third_party()` via `importlib.metadata` |
| Corpus storage | `core/qa/corpus_db.py` (NEW), `core/qa/corpus.py` | CREATE + UPDATE — SQLite implementation, JSON-to-SQLite migrator, identical public surface |
| Scene detection | `core/scene_detect.py` (NEW), `core/segmenter.py`, `core/models.py` | CREATE + UPDATE — `SegmentationConfig`, PySceneDetect adapter, segmenter dispatches by mode |
| SSCD metric | `core/qa/sscd.py` (NEW), `core/qa/report.py`, `core/qa/templates/report.html.j2`, `core/orchestrator.py`, `core/models.py` | CREATE + UPDATE — model loader+cache, frame extraction, QAReport.sscd field, HTML section, RunOptions flag |
| Target-VMAF loop | `core/segmenter.py`, `core/models.py`, `core/pipeline.py` | UPDATE — `EncoderConfig.target_vmaf`, re-encode-with-lower-CRF retry, max attempts cap |
| Calibrate metric | `core/calibration/loop.py`, `core/calibration/intensity.py` | UPDATE — `metric: Literal["chromaprint","sscd"]` parameter, SSCD-based delta evaluator |
| CLI | `src/yt_uniquifier/cli/cmd_qa.py`, `cmd_corpus.py` | UPDATE — `--sscd` flag on `yt-uniq qa`, `yt-uniq corpus migrate` subcommand |
| Docs | `docs/plugins.md`, `docs/sscd.md`, `docs/corpus.md`, `docs/profiles.md`, `docs/architecture.md` | CREATE + UPDATE |
| Tests | `tests/unit/test_transform_plugins.py`, `tests/unit/test_corpus_sqlite_parity.py`, `tests/unit/test_scene_detect.py`, `tests/unit/test_sscd_offline.py`, `tests/unit/test_target_vmaf_loop.py`, `tests/integration/test_sscd_real_ffmpeg.py`, `tests/integration/test_scene_segments_real.py` | CREATE |

---

## Round-by-round breakdown

### R1 — Transform plugin system (foundation)

**Goal**: Community can write transforms without forking. No new functionality; just discovery + safety wrapping.

- Wrap each built-in import in `core/transforms/__init__.py` with `try/except Exception as exc: log.warning("transform <name> failed to load: %s", exc)`. One broken transform must not kill the tool.
- After built-ins, call new `_discover_third_party()` that walks `importlib.metadata.entry_points(group="yt_uniquifier.transforms")` and imports each module by name. Same try/except guard.
- Declare the entry-points group in `pyproject.toml` `[project.entry-points."yt_uniquifier.transforms"]` (commented stub showing how a plugin registers).
- Add `tests/unit/test_transform_plugins.py`:
  - Synthesise a fake entry-point via `importlib.metadata.EntryPoint` monkey-patch; verify a registered `TransformSpec` becomes visible in the registry.
  - Verify a deliberately-broken plugin module (`raise ImportError("nope")`) logs a warning and does NOT prevent built-ins loading.
- `docs/plugins.md` — write a third-party "hello world" transform skeleton (8–12 lines).
- **Mirror**: `gui/workers/base.py` try/except logging pattern.
- **Validate**: `pytest tests/unit/test_transform_plugins.py -v && make lint && make typecheck`.
- **Commit**: `feat(core): v0.8.0 R1 — transform plugin system via entry_points`.

### R2 — SQLite-backed corpus (storage scale)

**Goal**: Replace JSON `index.json` (linear scan, full-file rewrite) with SQLite. Same public API. One-time migration on open.

- New `core/qa/corpus_db.py`:
  - `CorpusDB` class with the same methods `corpus.py::Corpus` exposes: `add_entry`, `lookup_by_id`, `iter_entries`, `purge`, `__len__`.
  - Schema: `entries(id PK, path, added_at, duration_sec, sample_count, phash_frames BLOB, audio_fp BLOB)`. Two BLOB columns hold packed `<Q`-encoded int arrays (struct), so no JSON parsing on read.
  - WAL mode, `PRAGMA synchronous=NORMAL`, single writer lock via `threading.RLock` (same model as `CheckpointStore`).
  - Index on `(added_at)` for "recent N" queries.
- Migration:
  - `from_json_dir(path) -> CorpusDB` reads legacy `index.json` once, inserts in a transaction, renames source to `index.json.migrated.<ts>` (don't delete — defensive).
  - First call to `open(dir)` auto-migrates if SQLite file absent and JSON present.
- Keep `corpus.py` as a thin facade for one release; internally delegates to `CorpusDB`. Deprecation comment, but no warning yet — keep noise low.
- CLI: `yt-uniq corpus migrate` subcommand for explicit invocation (idempotent).
- Tests `test_corpus_sqlite_parity.py`:
  - Round-trip: build via legacy JSON path → migrate → assert identical entries.
  - 10k entries insert benchmark stays under 1 s (sanity, not asserted hard).
  - Concurrent reads while a writer holds the lock don't deadlock.
- **Mirror**: `core/checkpoint.py` atomic-write + lock pattern.
- **Validate**: `pytest tests/unit/test_corpus_sqlite_parity.py -v && yt-uniq corpus migrate --dry-run`.
- **Commit**: `feat(core): v0.8.0 R2 — SQLite-backed corpus + one-time JSON migrator`.

### R3 — PySceneDetect segment boundaries (opt-in)

**Goal**: Adaptive content-aware segment boundaries as an alternative to keyframe-aligned. Falls back to keyframe if `[scene]` extra missing.

- `pyproject.toml`: `scene = ["scenedetect~=0.6.6"]`.
- `core/models.py`: new `SegmentationConfig(BaseModel, extra=forbid)` with `mode: Literal["keyframe","scene"] = "keyframe"`, `scene_threshold: float = 27.0`, `scene_min_length_sec: float = 2.0`. Add `Profile.segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)`.
- `core/scene_detect.py`: thin adapter. `detect_scene_boundaries(source, threshold, min_length) -> list[float]`. Lazy import; raises `PipelineError("install yt-uniquifier[scene]")` with clean message if module absent.
- `core/segmenter.py::plan_segments`: dispatch on `profile.segmentation.mode`. Scene boundaries are then **snapped to nearest keyframe** to preserve the stream-copy-extract invariant (load-bearing for split-process-concat). Document this constraint inline.
- Tests:
  - `test_scene_detect.py` (unit): boundaries on synthetic 3-cut testsrc2 video, ordering and min-length enforced. Skip with `pytest.importorskip("scenedetect")`.
  - `test_scene_segments_real.py` (integration): full plan with scene mode produces a different segment count than keyframe mode on the same input; resume still works.
- **Mirror**: optional-extra import pattern from `gui/workers/correlate_worker.py` (graceful skip on missing dep).
- **Validate**: `pytest tests/unit/test_scene_detect.py tests/integration/test_scene_segments_real.py -v`.
- **Commit**: `feat(core): v0.8.0 R3 — PySceneDetect segment boundary mode`.

### R4 — SSCD copy-detection metric

**Goal**: Optional QA metric using Meta's SSCD ResNet50 embedding. Lazy-load model from URL on first use, cache to `~/.cache/yt_uniquifier/models/`.

- `pyproject.toml`: `ml = ["torch>=2.1,<3", "torchvision>=0.16,<1"]`.
- `core/qa/sscd.py`:
  - `compute_sscd(source: Path, output: Path, *, frame_count: int = 32, cancel: CancelToken | None) -> SSCDResult`.
  - Frame extraction: ffmpeg `-vf "select=not(mod(n,K)),scale=288:288:force_original_aspect_ratio=increase,crop=288:288"` → PNG sequence via pipe (no temp dir bloat).
  - Model: `torch.jit.load("sscd_disc_mixup.torchscript.pt")`, eager-mode is fine since GPU is optional. SHA-256 verify after download.
  - Returns `SSCDResult(mean_similarity, min_similarity, per_frame: tuple[float, ...])`. Cosine on 512-d embeddings.
- `core/models.py::QAReport`: add `sscd: SSCDResult | None = None`.
- `core/orchestrator.py::RunOptions`: `compute_sscd: bool = False`.
- `core/qa/report.py::build_report`: when `options.compute_sscd`, call `compute_sscd(source, output)` between VMAF and CID predict; respect `cancel_token`.
- HTML template: new `sscd_section.j2` partial — per-frame chart + headline `mean_similarity` colored by band (≥0.85 = problem, 0.65–0.85 = caution, <0.65 = clean).
- CLI: `yt-uniq qa <in> <out> --sscd` flag.
- Tests:
  - `test_sscd_offline.py`: monkey-patch the torch model loader to return a deterministic stub (random unit vectors with fixed seed). Verify result shape, cosine math, cancel mid-frame.
  - `test_sscd_real_ffmpeg.py` (integration, behind `@pytest.mark.ml` + `pytest.importorskip("torch")`): full SSCD on tiny_clip vs itself → similarity ≈ 1.0.
- **Mirror**: `core/qa/vmaf.py` subprocess + parse pattern; `core/qa/audio_fp.py` optional-binary graceful skip.
- **Validate**: `pytest tests/unit/test_sscd_offline.py -v && pytest -m ml tests/integration/test_sscd_real_ffmpeg.py -v` (only on hosts with `[ml]` installed).
- **Commit**: `feat(core,qa): v0.8.0 R4 — SSCD-based copy detection metric`.

### R5 — per-segment VMAF target-quality (Av1an-style)

**Goal**: If a segment's post-encode VMAF is below `target_vmaf`, re-encode that segment with CRF reduced by `step` (default −2). Cap at `max_retries` (default 2). Single-host only — distributed batch logs a warning and skips the loop.

- `core/models.py`: `EncoderConfig` gains `target_vmaf: float | None = None`, `target_vmaf_step: int = 2`, `target_vmaf_max_retries: int = 2`.
- `core/segmenter.py::process_video_segment`:
  - After successful encode, when target set, run `core/qa/vmaf.py::compute_segment_vmaf(seg_in, seg_out, subsample=12)` against the same source span used for extract.
  - If `< target`, rebuild command with `crf = current_crf - step`, retry. Log structured event `target_vmaf_retry`.
  - Emit `RunEvent(kind="target_vmaf", payload={"segment": idx, "vmaf": v, "crf": c, "attempt": n})`.
- Plan hash must include `target_vmaf` fields (already covered by `Profile` JSON dump — verify).
- Distributed worker (`cli/cmd_worker.py`): on encounter, log `WARNING: target_vmaf ignored in distributed mode`.
- Tests `test_target_vmaf_loop.py`:
  - Monkey-patch `compute_segment_vmaf` to return scripted values; assert correct number of retries, final CRF, structured events.
  - When all retries exhausted, segment finalises anyway with last attempt and emits `target_vmaf_failed` event (no crash).
- **Mirror**: existing retry policy in `core/runner.py` for retry accounting + log lines preservation.
- **Validate**: `pytest tests/unit/test_target_vmaf_loop.py -v && make typecheck`.
- **Commit**: `feat(core): v0.8.0 R5 — per-segment VMAF target-quality feedback loop`.

### R6 — Calibrate via SSCD (metric option)

**Goal**: `core/calibration/loop.py::calibrate(...)` gains a `metric: Literal["chromaprint","sscd"] = "chromaprint"` parameter. SSCD path requires `[ml]` extra; clean error if missing.

- `loop.py`: dispatch on `metric` to either existing chromaprint Jaccard evaluator or a new `_evaluate_sscd(source, candidate, cancel) -> float` thin wrapper around `core/qa/sscd.py`. Lower is more divergent for both metrics (invert SSCD: `1 - mean_similarity`).
- CLI: `yt-uniq calibrate --metric sscd ...`.
- GUI: Calibrate screen gains a radio toggle (purely additive; defaults preserve current behaviour).
- Tests:
  - Unit: monkey-patched evaluator confirms bisect terminates and `metric` parameter selects the right code path.
  - Cancel mid-iteration still honoured (covered by existing v0.5.5 A6 fix; just regression-guard).
- **Mirror**: `loop.py` existing metric fallback pattern (VMAF → SSIM → pHash).
- **Validate**: `pytest tests/unit/test_calibrate_sscd_metric.py -v`.
- **Commit**: `feat(core): v0.8.0 R6 — calibrate via SSCD metric option`.

### R7 — docs + completeness + acceptance gate

**Goal**: Documentation, cross-cutting smoke test, mark master plan items done.

- `docs/plugins.md` — third-party transform tutorial + entry-points snippet.
- `docs/sscd.md` — what SSCD is, license + model provenance, threshold bands, perf notes (CPU vs GPU), `[ml]` install command, correlation with real CID.
- `docs/corpus.md` — SQLite schema, migration, scale numbers.
- `docs/profiles.md` — add `segmentation:`, `encoder.target_vmaf:` rows + scene mode warning (snapped to keyframe).
- `docs/architecture.md` — extend RunEvent contract table with new `target_vmaf` / `target_vmaf_failed` / `target_vmaf_retry` kinds.
- `tests/integration/test_v080_smoke.py` — single end-to-end run combining: SQLite corpus + scene segmentation (if `[scene]` available) + SSCD (if `[ml]` available) + target_vmaf. Skips politely when extras absent.
- Update `.claude/plans/yt-uniquifier-best-in-class.plan.md` v0.8.0 checkboxes to `[x]` with round commits.
- **Validate**: `make check` end-to-end zero failures across 3-OS matrix; `pytest tests/integration/test_v080_smoke.py -v`.
- **Commit**: `docs+test: v0.8.0 R7 — docs, smoke, master-plan checkboxes`.

---

## Validation (after each round)

```bash
make lint                    # ruff
make typecheck               # mypy --strict
make test-unit               # ~10s baseline
make check                   # full pipeline (gate)
pytest tests/unit/test_<round-specific>.py -v
```

Per-round integration smoke (when applicable):

```bash
pytest tests/integration/test_scene_segments_real.py -v   # R3
pytest -m ml tests/integration/test_sscd_real_ffmpeg.py -v  # R4 (only on hosts with [ml])
pytest tests/integration/test_v080_smoke.py -v            # R7
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `torch` is heavy (~800 MB on disk with CUDA wheels) → contributors balk at `[ml]` extras | High | CPU-only wheels (`torch+cpu` index) documented prominently; `[ml]` clearly optional; never imported at module top |
| SSCD model URL goes 404 over time | Medium | Pin commit hash; cache once; bundle SHA-256 expected value; fallback URL list; `yt-uniq qa --sscd` errors with explicit message |
| PySceneDetect boundaries that DON'T fall on keyframes break stream-copy-extract invariant | High | Snap detected boundaries to nearest keyframe before passing to `plan_segments`; document the snap explicitly; add regression test that asserts every emitted boundary is a keyframe |
| F11 target-VMAF feedback loop interacts badly with parallel `ThreadPoolExecutor` segment workers (RunEvent ordering) | Medium | Retry event already has `attempt` counter; GUI uses (segment, attempt) tuple; covered by v0.5.5 A1 frozen-payload contract |
| SQLite migration on a corrupt `index.json` deletes user data | Low | Migration renames source to `.migrated.<ts>`, never unlinks; explicit `yt-uniq corpus migrate` CLI subcommand for opt-in control |
| `entry_points` discovery loads malicious third-party code on import | Medium | Document explicitly that plugins run user-trusted code; same trust model as any pip package; try/except logs but does not silently consume |
| Distributed worker silently degrades when `target_vmaf` set | Medium | Explicit `WARNING` log line + structured event so it surfaces in batch reports |
| R3 + R5 combination — scene-detected segments don't have stable indices across resume (different scene-detect runs) | Medium | Cache scene boundaries in `state.json` keyed by source `(size, mtime_ns)` (same trick as v0.6 B1 keyframe cache); reused on resume |

---

## Out of scope (deferred)

- SSCD on the **fly** (per-segment, mid-encode) — adds GPU contention and complicates target-VMAF retries. R7 final report only.
- Replacing chromaprint in the QA report — SSCD is **additive**, chromaprint stays.
- Plugin marketplace UI (that's v0.9 F9).
- Telemetry on plugin loads (also v0.9).
- Whisper subtitle filter (v0.9 F14).
- Vulkan AV1 was already shipped in v0.6 F8 — confirmed in master plan.

---

## Acceptance

- [ ] R1–R7 all merged, each as one atomic commit, `make check` green at every commit boundary
- [ ] `yt-uniq qa <in> <out> --sscd` works end-to-end on a host with `[ml]` installed
- [ ] `yt-uniq corpus migrate` converts a legacy `index.json` and parity-checks identical entries
- [ ] A third-party transform plugin example loads via `entry_points` and registers a working filter chain
- [ ] PySceneDetect mode produces different boundaries than keyframe mode on a multi-cut source, AND snapped boundaries are all keyframes
- [ ] `target_vmaf` on a segment that initially scores low triggers a re-encode with reduced CRF, surfaced as a `RunEvent`
- [ ] `yt-uniq calibrate --metric sscd` converges on a synthetic source
- [ ] `docs/plugins.md`, `docs/sscd.md`, `docs/corpus.md` shipped; `docs/profiles.md` + `docs/architecture.md` updated
- [ ] Master plan `.claude/plans/yt-uniquifier-best-in-class.plan.md` v0.8.0 row marked `[x]` with commit refs

---

**WAITING FOR CONFIRMATION**. Options:

- **"yes"** / **"proceed R1"** — start with R1 (transform plugin system, smallest blast radius)
- **"order: R<n>,R<m>,…"** — re-sequence rounds (e.g. SSCD first if you'd rather front-load the ML risk)
- **"split R<n>"** — break a round into smaller commits (R4 SSCD is the biggest; happy to split frame-extraction from model-load from HTML)
- **"drop R<n>"** — skip a round entirely from this release
- **"expand R<n>"** — write a detailed sub-plan for one round before starting
