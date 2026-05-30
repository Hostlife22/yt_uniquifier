# GUI Test Coverage Matrix

Snapshot taken 2026-05-30 against the v0.5.x GUI (10 screens, 14 workers).

## Existing tests

Total collected: **69** (`pytest tests/unit/test_gui_* tests/smoke/ --collect-only`).

### Screens

| Screen | Functional | Smoke (open + navigate) | Widget-level unit | Integration (worker hookup) |
|---|---|---|---|---|
| `run` | yes | full-launch sweep only | — | — |
| `batch` | yes | full-launch sweep only | — | — |
| `calibrate` | yes | full-launch sweep only | — | — |
| `qa_viewer` | yes | full-launch sweep only | — | — |
| `profile_editor` | yes | full-launch sweep only | `test_gui_profile_editor.py` (3) | — |
| `history` | yes | full-launch sweep only | `test_gui_history.py` (3) | — |
| `corpus` | yes | full-launch sweep only | `test_gui_corpus_settings.py` (1 screen test) | — |
| `queue` | yes | full-launch sweep only | — | — |
| `validation` | yes | full-launch sweep only | `test_gui_validation_screen.py` (3) | — |
| `settings` | yes | full-launch sweep only | `test_gui_corpus_settings.py` (settings half, 3) | — |

`tests/smoke/test_gui_full_launch.py::test_main_window_launches_and_navigates`
only checks `sidebar.count == 10` and that `currentIndex` follows the row.
It does **not** assert any child widgets exist on each screen.

### Workers

| Worker | Unit tests |
|---|---|
| `base` | `test_gui_workers.py` (3 — base + cancel + QThread sanity) |
| `run_worker` | `test_gui_workers.py` (2 — resolves + cancel) |
| `batch_worker` | `test_gui_batch_worker.py` (5) |
| `calibrate_worker` | `test_gui_calibrate_worker.py` (3) |
| `qa_worker` | `test_gui_qa_worker.py` (2) |
| `queue_worker` | `test_gui_queue_workers.py` (2) |
| `queue_status_worker` | `test_gui_queue_workers.py` (2) |
| `corpus_worker` | `test_gui_corpus_settings.py` (2) |
| `probe_worker` | **none** |
| `preflight_worker` | **none** |
| `corpus_list_worker` | **none** |
| `queue_io_worker` | **none** |
| `generate_variants_worker` | **none** |
| `correlate_worker` | partly via `test_gui_validation_screen.py` |
| `encoder_detect_worker` | **none** |

### Widgets & state

- `test_gui_app_state.py` — 7 (recents, history persistence, theme signal)
- `test_gui_widgets.py` — 15 (FilePicker, PreflightPanel, SegmentTimeline, LogConsole, KpiPills)
- `test_gui_chart_widget.py` — 4
- `test_gui_theme.py` — 4
- `test_gui_imports.py` — 3 (import-only)

## Gaps to close

| Gap | Phase |
|---|---|
| No per-screen smoke asserting required widgets exist | Phase 2 |
| No e2e covering Run → ffmpeg → QA flow through the UI | Phase 3 |
| No e2e for Batch/Queue/Calibrate/QA Viewer integration | Phase 3 |
| 8 workers uncovered or partially covered | Phase 4 |
| No exploratory manual script + checklist | Phase 5 |
| No screenshot baselines for visual regression | Phase 6 |

## Commands

```bash
make test-unit                                      # unit only
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
    tests/smoke/ tests/unit/test_gui_* -q           # all GUI unit + smoke
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
    tests/integration/test_gui_*_e2e.py -m integration -q   # phase 3
QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
    tests/visual/ -m visual -q                      # phase 6 (linux-offscreen only)
```
