# Plan: Bug-Triage Follow-Ups (2026-05-31)

**Source**: `docs/bug-triage-2026-05-31.md` (Backlog section)
**Selected items**:
1. GUI deep sweep on real window (not `QT_QPA_PLATFORM=offscreen`)
2. Rubberband perf characteristic on 4K / 5-min+ content (4 timeout cells)
**Complexity**: Medium (item 1 = light tooling + manual run; item 2 = perf research + preflight WARN + docs)

---

## Summary

Two backlog items from the 2026-05-31 real-video matrix triage. Item 1 closes the GUI gap left by the CLI-only matrix sweep — the visual harness today runs only `QT_QPA_PLATFORM=offscreen`, which silently masks compositor/font/HiDPI defects. Item 2 turns the known-but-undocumented rubberband perf cost into a preflight WARN so users on long/4K content get a heads-up before a multi-hour encode, plus a measured baseline so future tuning has numbers to beat.

Neither item changes core encoding logic. Item 1 is tooling + a manual checklist. Item 2 is one preflight check, one docs paragraph, and one benchmark script invocation.

---

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Preflight WARN | `src/yt_uniquifier/core/preflight.py:202` (`_check_pitch_rubberband`) | Plan iteration → conditional finding with `code`/`severity`/`message`/`suggestion`; registered in `preflight()` |
| Preflight test | `tests/unit/test_pitch_rubberband.py` | Build minimal `Plan` via fixture, call check directly, assert finding code present |
| Visual/GUI test | `tests/visual/test_gui_screenshots.py:40` (`main_window` fixture) | `QApplication.instance() or QApplication([])`, `MainWindow()`, sidebar row iteration |
| GUI worker boundary | `src/yt_uniquifier/gui/workers/run_worker.py` | Worker wraps `core/` callable, bridges `on_event` → `pyqtSignal`; never reimplements pipeline |
| Benchmark CSV | `tools/benchmark.py` | Wall + RSS + per-phase via `on_event` callback; appends one row to `--csv` |
| Docs perf note | `docs/profiles.md` "Performance notes" (referenced at `bug-triage-2026-05-31.md:381`) | Profile-vs-input wall-time matrix table |

---

## Files to Change

| File | Action | Why |
|---|---|---|
| `tools/gui_sweep.py` | CREATE | Drive `MainWindow` through all 10 screens on real (non-offscreen) Qt platform; capture PNG per screen + console log; write Markdown report |
| `docs/gui_sweep.md` | CREATE | Manual sweep checklist — what to click on each screen, what to verify, expected output of `tools/gui_sweep.py` |
| `src/yt_uniquifier/core/preflight.py` | UPDATE | Add `_check_rubberband_perf` WARN when rubberband enabled AND (`source.duration_sec > 60` OR `source.video.height > 1080`); register in `preflight()` after `_check_pitch_rubberband` |
| `tests/unit/test_pitch_rubberband.py` | UPDATE | 3 new tests: short+SD passes silently, long-clip warns, 4K warns, both-conditions emits single WARN (not two) |
| `docs/profiles.md` | UPDATE | Add subsection "Rubberband perf characteristic" — wall-time matrix from `bug-triage-2026-05-31.md` §9, link to preflight code |
| `docs/qa_report.md` | NO CHANGE | (Item 4 already documented per closed-out checklist) |
| `tools/benchmark.py` | NO CHANGE | Reuse as-is for the perf baseline run |
| `out/runs/rubberband_baseline.csv` | CREATE (generated) | Output of `tools/benchmark.py` on 4 timeout cells + 2 control cells (numbers feed docs table) |

No code changes to `core/transforms/audio_pitch.py`, `core/orchestrator.py`, `core/pipeline.py`, or any GUI screen/worker — out of scope.

---

## Tasks

### Task 1 — Real-window GUI sweep harness (`tools/gui_sweep.py`)

- **Action**: New script that:
  1. Asserts `QT_QPA_PLATFORM` is NOT `offscreen` (fail fast with hint).
  2. Boots `MainWindow`, resizes to `1280×800`, iterates `SCREENS` (same mapping as `tests/visual/test_gui_screenshots.py`).
  3. For each screen: `setCurrentRow(idx)`, `app.processEvents()`, sleep 250 ms, `grab()` → PNG into `out/gui_sweep/<timestamp>/<label>.png`.
  4. Captures stderr/stdout (Qt warnings via `qInstallMessageHandler`) into `out/gui_sweep/<timestamp>/qt.log`.
  5. On `--smoke-worker` flag: also fires a tiny `RunWorker` against `tests/fixtures/.gen/clip_a.mp4 × soft.yaml` and waits for `finished` signal (timeout 60 s); verifies no exceptions on the bus.
  6. Writes `out/gui_sweep/<timestamp>/report.md` with screen list, file links, Qt log excerpt, smoke-worker exit status.
- **Mirror**: `tests/visual/test_gui_screenshots.py:40` for `MainWindow` boot; `tools/benchmark.py` for argparse + report-row pattern.
- **Validate**: `python tools/gui_sweep.py --out out/gui_sweep` exits 0 on a host with a real display and writes 10 PNGs + 1 report.md.

### Task 2 — GUI manual-sweep checklist (`docs/gui_sweep.md`)

- **Action**: New doc with three sections:
  1. **Setup** — macOS native run (`make dev` then `.venv/bin/python -m yt_uniquifier.gui.app_pyqt`), how to force native vs offscreen, how to run `tools/gui_sweep.py`.
  2. **Per-screen checklist** — for each of 10 screens (Run / Batch / Calibrate / QA Viewer / Profile Editor / History / Corpus / Queue / Validation / Settings): minimum interactions (pick file, change profile, click Run, observe progress, observe QA, switch tabs) + expected behaviour + known-good screenshot reference.
  3. **Triage table** — empty table to fill on each sweep with columns: date / screen / pass-fail / Qt-warnings / notes.
- **Mirror**: `docs/architecture.md` heading hierarchy and tone; `docs/qa_report.md` for "What to look for" framing.
- **Validate**: `make lint` (no broken md if pre-commit md-lint exists); manual `mdcat docs/gui_sweep.md` reads cleanly.

### Task 3 — Preflight WARN: rubberband on long / hi-res sources

- **Action**: Add `_check_rubberband_perf(plan: Plan) -> list[PreflightFinding]` to `core/preflight.py`:
  - Trigger condition: any enabled `audio.pitch_tempo` with `method=='rubberband'` AND (`plan.source.duration_sec > 60` OR `plan.source.video.height > 1080`).
  - Code: `audio.pitch.rubberband.slow`.
  - Severity: `warn` (NOT `fail` — the encode still finishes, just slowly).
  - Message includes measured multiplier: e.g. `"rubberband on 4K/long content runs ~10-15× wall time vs asetrate (see docs/profiles.md#rubberband-perf-characteristic)"`.
  - Suggestion: `"For throughput on long or >=4K sources, switch the profile to method='asetrate'. Keep 'rubberband' when formant preservation matters more than wall time."`.
  - Wire into `preflight()` immediately after `_check_pitch_rubberband(plan)` (line 49) so the FAIL path still short-circuits on missing filter.
- **Mirror**: `_check_pitch_rubberband` for structure; reuse `plan.profile.transforms` iteration idiom.
- **Validate**: `pytest tests/unit/test_pitch_rubberband.py -q` (after Task 4 lands).

### Task 4 — Unit tests for the new WARN

- **Action**: Extend `tests/unit/test_pitch_rubberband.py`:
  - `test_rubberband_short_sd_passes_silently` — 30 s 720p source, `cid_aware` → no `audio.pitch.rubberband.slow` finding.
  - `test_rubberband_long_source_warns` — 120 s 720p source → emits `audio.pitch.rubberband.slow` once.
  - `test_rubberband_4k_warns` — 12 s 2160p source → emits `audio.pitch.rubberband.slow` once.
  - `test_rubberband_long_and_4k_emits_single_warn` — both conditions true → still exactly one finding (no duplicates).
  - `test_rubberband_disabled_never_warns` — `method='asetrate'` long+4K → zero `audio.pitch.rubberband.slow` findings.
- **Mirror**: existing fixture style in `tests/unit/test_pitch_rubberband.py` for Plan construction.
- **Validate**: `pytest tests/unit/test_pitch_rubberband.py -v`.

### Task 5 — Capture baseline numbers + update `docs/profiles.md`

- **Action**:
  1. Run `tools/benchmark.py` on the 4 timeout cells (`cid_aware × {synth_sdr_4k, synth_long_5min}`, `cid_aggressive × {same}`) plus 2 controls (`soft × clip_long`, `medium × clip_long`) with `--csv out/runs/rubberband_baseline.csv`. Use `--workers 1` (matches matrix). Allow up to 4 hours wall total — these are the slow cells.
  2. Add a "Rubberband performance characteristic" subsection to `docs/profiles.md` with:
     - 1-paragraph explanation (why rubberband is slow, why we still default to it for `cid_*`)
     - Wall-time matrix lifted from `bug-triage-2026-05-31.md` §9 + new baseline rows
     - Link to preflight code (`core/preflight.py::_check_rubberband_perf`)
     - Workaround snippet (yaml diff that swaps `method: rubberband` → `method: asetrate` in user's local profile copy)
- **Mirror**: existing perf table in `docs/profiles.md` (whatever schema is currently there) — match column order and units.
- **Validate**: `python tools/real_video_matrix.py --profile cid_aware --inputs synth_sdr_4k --timeout 1` quickly verifies the new WARN fires (encode aborts at preflight stage well before timeout).

### Task 6 — (Optional, defer if scope creep) Defense-in-depth rubberband revalidation

- **Action**: NOT in this plan. Logged at `bug-triage-2026-05-31.md:117-123` as future work. Item already has a code-path note in `core/audio_windows.py`. Revisit if matrix re-run after these tasks shows new false-pass behaviour.

---

## Validation

```bash
# Task 1+2: harness + checklist
QT_QPA_PLATFORM=cocoa .venv/bin/python tools/gui_sweep.py --out out/gui_sweep
ls out/gui_sweep/*/report.md            # exists
ls out/gui_sweep/*/*.png | wc -l        # == 10

# Task 3+4: preflight WARN
pytest tests/unit/test_pitch_rubberband.py -v
make lint && make typecheck

# Task 5: baseline + docs
python tools/benchmark.py tests/fixtures/.gen/synth_long_5min.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out /tmp/bench_out.mp4 --encoder libx264 --workers 1 \
  --csv out/runs/rubberband_baseline.csv

# Full regression (CI gate)
make check
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Real-window sweep needs a logged-in macOS session (no headless CI) | High | Document as "developer-only, not CI" in `docs/gui_sweep.md`; keep offscreen `tests/visual/test_gui_screenshots.py` as the CI gate |
| WARN noise irritates users running short test renders that just happen to be >60 s | Medium | Threshold is `>60s` AND single `warn` severity (not `fail`); message names exact override (`method='asetrate'`); one-shot per run |
| Baseline numbers in `docs/profiles.md` go stale as ffmpeg/rubberband evolves | Medium | Date-stamp the table ("Measured 2026-05-31 on evermeet ffmpeg 8.1.1 + librubberband"); add note to refresh on every yearly matrix re-run |
| `_check_rubberband_perf` order-of-checks bug — fires BEFORE `_check_pitch_rubberband` and shadows the FAIL | Low | Test `test_missing_rubberband_filter_still_fails_not_warns` ensures FAIL wins; wire order in `preflight()` is explicit |
| Benchmark run on 5-min × cid_aggressive may exceed 4 hr | Low | Pre-document expected wall time; allow `--skip` flag in case operator wants to stop after first 3 cells and accept partial baseline |
| `tools/gui_sweep.py` flakes on Wayland or VM display | Medium | Macro-defensive: catch + log Qt errors, don't fail script; rely on operator reading report.md |

---

## Acceptance

- [ ] `tools/gui_sweep.py` exists, runs on a real macOS session, writes 10 PNGs + `report.md`
- [ ] `docs/gui_sweep.md` documents setup + per-screen checklist + triage table
- [ ] `_check_rubberband_perf` lives in `core/preflight.py` and is wired into `preflight()`
- [ ] 5 new unit tests in `tests/unit/test_pitch_rubberband.py` pass; existing tests still pass
- [ ] `docs/profiles.md` has a "Rubberband performance characteristic" subsection with measured matrix and workaround
- [ ] `out/runs/rubberband_baseline.csv` exists with ≥4 rows (4 timeout cells); controls captured if time permits
- [ ] `make check` is green
- [ ] `docs/bug-triage-2026-05-31.md` "Backlog" section updated — both items moved to "Closed by follow-up" or referenced by this plan path
- [ ] Patterns mirrored, not reinvented (preflight idiom, fixture style, benchmark CSV format)
