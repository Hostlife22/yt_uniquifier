# GUI manual sweep

The PyQt6 GUI has 10 screens and 14 background workers. The offscreen
visual regression suite (`tests/visual/test_gui_screenshots.py`,
`QT_QPA_PLATFORM=offscreen`) catches widget-shape regressions, but it
cannot catch compositor / font / HiDPI / drag-drop / dialog defects that
only manifest on a real display. This doc covers the **developer-only**
manual sweep that fills that gap.

There is no CI hook — these checks require a logged-in macOS (or Linux
with a real X / Wayland session). Run it locally before a release tag,
after touching any `gui/screens/*` or `gui/workers/*`, and after a PyQt6
upgrade.

## Setup

```bash
make dev                                                # installs PyQt6
# Real-window run, native platform:
.venv/bin/python -m yt_uniquifier.gui.app_pyqt          # interactive
# OR drive the harness (fast, automated, captures PNG + Qt log):
.venv/bin/python tools/gui_sweep.py                     # writes out/gui_sweep/<ts>/
.venv/bin/python tools/gui_sweep.py --smoke-worker      # + tiny RunWorker pass
```

`tools/gui_sweep.py` REFUSES `QT_QPA_PLATFORM=offscreen` — that's
covered by the visual regression suite. On macOS the script uses the
`cocoa` plugin; on Linux it inherits `QT_QPA_PLATFORM` (`xcb` or
`wayland`). The harness writes:

| File | Purpose |
|---|---|
| `out/gui_sweep/<ts>/<screen>.png` | Per-screen 1280×800 PNG |
| `out/gui_sweep/<ts>/qt.log` | Captured Qt messages (warnings, criticals, fatals) |
| `out/gui_sweep/<ts>/report.md` | Triage checklist with image links |

Read `report.md` after each run — Qt warnings about missing translation
files or HiDPI mismatch usually surface there before they reach a user.

## Per-screen checklist

For each screen: open `MainWindow`, click into the sidebar entry, then
walk through "Verify" items. **Failure** = anything that crashes, hangs,
silently no-ops, leaks worker threads, or shows visibly wrong content.

### 1. Run

- **Interactions**: drag a file onto the input field; pick a profile;
  click "Probe"; click "Preflight"; click "Run" with default encoder.
- **Verify**: progress bar advances; status bar updates per segment;
  cancel button works mid-run; QA report opens on completion.
- **Watch for**: orphaned ffmpeg subprocesses after cancel
  (`pgrep -fl ffmpeg` after click).

### 2. Batch

- **Interactions**: load a directory of fixtures; pick a profile; set
  workers=2; click "Run batch".
- **Verify**: per-file rows update independently; pause works; resume
  picks up at the last completed file.
- **Watch for**: row state desync with worker (e.g., row shows "done"
  but worker still running).

### 3. Calibrate

- **Interactions**: pick a reference clip; pick a starting profile;
  click "Calibrate"; watch iteration log.
- **Verify**: each iteration emits a fingerprint delta; suggested
  profile diff renders; "Apply" writes the new YAML.
- **Watch for**: calibration loop not converging within the iteration
  cap (currently 6) — known acceptable, but log a perf note.

### 4. QA Viewer

- **Interactions**: open an existing `.qa.json` from `out/runs/`;
  switch between video / audio / fingerprint tabs.
- **Verify**: charts render (matplotlib backend); table sort works;
  per-segment thumbnail strip loads.
- **Watch for**: `audio_fp_similarity == 0.0` is expected (see
  `docs/qa_report.md` Finding #4) — don't flag as bug.

### 5. Profile Editor

- **Interactions**: open `cid_aware.yaml`; toggle a transform;
  edit a numeric param; save as new file.
- **Verify**: pydantic validation fires on bad input; diff vs
  original shown before save; new file readable by `yt-uniq run`.
- **Watch for**: silent overwrite of the original — should always
  prompt with a save-as dialog.

### 6. History

- **Interactions**: scroll the run log; double-click a row to open
  its QA report.
- **Verify**: rows show latest first; "Open output" reveals the
  produced mp4 in Finder/Files.
- **Watch for**: rows accumulating unbounded — known: no auto-prune
  in v0.5; file a separate ticket if list >10 000 rows.

### 7. Corpus

- **Interactions**: generate the synthetic corpus
  (`_corpus_gen.py` is invoked); confirm `tests/fixtures/.gen/` is
  populated.
- **Verify**: progress per fixture; cancel works; rerun is idempotent
  (skips existing files unless `--force`).
- **Watch for**: cross-process race when multiple GUIs run corpus gen
  at the same time — known acceptable, file lock is best-effort.

### 8. Queue

- **Interactions**: enqueue 3 jobs; pause the queue; reorder rows;
  resume.
- **Verify**: queue state persists across app restart;
  `state.json` files for each job are valid.
- **Watch for**: queue worker not picking up after restart (known
  edge case if `state.json` was mid-write during crash).

### 9. Validation

- **Interactions**: pick a "real CID" log (CSV or jsonl); click
  "Correlate"; review the per-segment table.
- **Verify**: correlation runs to completion; results highlight
  rows above the CID threshold; export to CSV works.
- **Watch for**: matplotlib widget leak on repeated runs (known on
  some macOS versions; force-reload the screen as workaround).

### 10. Settings

- **Interactions**: change theme; toggle "show preflight as modal";
  set custom ffmpeg path; click Save.
- **Verify**: changes persist via `AppState` to `~/.yt_uniquifier/`;
  invalid ffmpeg path shows inline error.
- **Watch for**: theme switch requiring restart — currently does
  require it; documented in tooltip.

## Triage table

Fill this in (or copy-paste from generated `report.md`) on each sweep.
Keep the most recent 5 entries inline; archive older runs to
`out/gui_sweep/<ts>/report.md`.

| Date | Operator | Qt platform | Pass / Total | Qt warns | Smoke worker | Notes |
|---|---|---|---|---|---|---|
| _YYYY-MM-DD_ | _name_ | cocoa / xcb / wayland | _X_ / 10 | _N_ | PASS / FAIL / skip | _link to run dir_ |

## Related

- `tests/visual/test_gui_screenshots.py` — CI baseline (offscreen)
- `tools/gui_sweep.py` — this harness
- `docs/gui.md` — screen reference and architecture
- `docs/bug-triage-2026-05-31.md` — closed by this doc + harness
