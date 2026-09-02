# GUI guide

`yt-uniq-gui` is the desktop UI for yt-uniquifier. It mirrors the CLI
1:1 — anything you can do from `yt-uniq <cmd>` you can do here, plus
some extras (Profile Editor, run History, embedded QA viewer, Validation
wizard).

## Install + launch

```bash
pip install 'yt-uniquifier[gui]'
yt-uniq-gui
```

`PyQt6-WebEngine` (~150 MB) enables the embedded QA Viewer. If absent
or running headless (e.g. CI smoke), the QA Viewer falls back to a
label + "Open in browser" button.

## Screen overview

The left sidebar has 10 entries. Click to switch; navigation calls each
screen's `on_show()` hook so state stays fresh.

### 1. Run

Single-file uniquification.

Flow: drop input → auto-probe (codec / resolution / fps / HDR) → pick
profile + encoder → optional "Run preflight" → ▶ Run → segment timeline
animates while the orchestrator processes → KPI pills appear with the
QA verdict → "Open QA report" opens the HTML in your browser.

KPI pills colour-code per band:
- **pHash worst chunk** — green < 0.75, yellow < 0.85, red ≥ 0.85
- **VMAF mean** — green ≥ 85, yellow ≥ 75, red < 75
- **Audio FP Hamming** — green ≥ 18 bits, yellow ≥ 10 bits, red < 10
- **CID predicted** — green < 0.2, yellow < 0.4, red ≥ 0.4

### 2. Batch

Directory-of-files iterator. Pick input dir + output dir + glob
pattern (default `*.mp4`) + profile + encoder. The matched files preview
in a table; click ▶ Run batch and each row updates its status
(pending → running → done / failed) as the worker progresses.

"Continue on error" keeps the batch going past per-file failures (the
default); uncheck to stop on the first failure.

### 3. Calibrate

Auto-tune profile intensity to a target self-match. Pick source +
base profile + target self-match (default 0.2) + min quality (default
88) + iterations (default 5) + clip seconds (default 60). The
convergence chart plots three series per iteration:
`intensity_factor`, `self_match`, `quality / 100`. When done, click
"Save tuned profile as…" to write the result.

If the calibrate loop didn't converge ("⚠ best-so-far"), the saved
profile is still the best candidate seen, just not within the quality
floor — bump `--clip-sec` or relax `--min-quality` and try again.

### 4. QA Viewer

Two modes:
- **Open existing** — pick any `.qa.html` and render it embedded.
- **Compute new** — pick (input, output) pair and compute the report
  inline. Output paths are written next to the output file as
  `<output>.mp4.qa.json` / `.qa.html`.

### 5. Profile Editor

Inline YAML profile editing. Pick a profile from the dropdown; the
transforms table loads with three columns:
- transform id (red if not in the current registry)
- enabled checkbox
- params as JSON (editable)

Top-level `seed_strategy` selector below. Live YAML preview on the
right. "Save" overwrites with a `.yaml.bak` backup; "Save as…" prompts
for a new path.

Invalid JSON in any params cell prevents save and shows a QMessageBox
with the row number.

### 6. History

Last 100 runs (capped, persisted to
`~/.config/yt_uniquifier/history.json`). Per-row "Open output" + "QA"
buttons. Filter box matches across source / profile / encoder /
status. "Clear all" wipes the file.

### 7. Corpus

CRUD over the local fingerprint index. Add a file → CorpusWorker
indexes it in the background (multi-second per long video). Remove
selected rows by ID.

### 8. Queue

Two sub-tabs:
- **Queue management** — pick the queue root (must be on a shared FS
  with atomic-rename support), "Init queue here" creates pending/
  in_progress/ done/ failed/, "Add files…" hardlink-copies files into
  pending/, live stats banner refreshes every 2 s.
- **Worker control** — start a single-process QueueWorker drainer:
  lease → run_full → release_done/failed. Stop is a cooperative
  cancel.

### 9. Validation

3-step wizard for the v0.4.1 real-CID validation harness:
- **Generate** — N variants of one source. Calls
  GenerateVariantsWorker (wraps `tools/generate_variants.py`).
- **Record** — editable table with per-variant predicted KPIs +
  empty cells for `upload_date`, `youtube_video_id`, `match_status`,
  `notes`. "Save to validation_log.csv" appends.
- **Analyze** — runs `python tools/validation_correlate.py` against
  the CSV and shows the output (Spearman correlation per predictor).

Manual upload between steps 1 and 2: see
[docs/validation_harness.md](./validation_harness.md) for the full
loop.

### 10. Settings

- **Theme** — dark / light / system (system falls back to dark in MVP);
  live-applies via state.theme_changed signal.
- **Default profile** — pre-selected on the Run / Batch / Calibrate
  screens.
- **Maintenance** — Reset encoder cache (deletes
  `~/.cache/yt_uniquifier/encoders.json`), Open log dir, Open config dir.

## Keyboard shortcuts

All primary CTAs and sidebar navigation are keyboard-reachable. Screen-
reader users can rely on `setAccessibleName` + `setAccessibleDescription`
on every interactive widget (regression test:
`tests/unit/test_gui_accessibility.py`).

### Global navigation

| Shortcut | Action |
|---|---|
| Cmd/Ctrl+1..0 | Switch to sidebar entry 1..10 (v0.7.0) |
| Cmd/Ctrl+W   | Close window |
| Cmd/Ctrl+Q   | Quit |

### Run screen

| Shortcut | Action |
|---|---|
| Cmd/Ctrl+R | **Run** — start the encode (alias: mnemonic `&Run`) |
| Esc        | **Cancel** — stop at the next safe boundary (alias: mnemonic `&Cancel`) |
| Space      | **Pause / Resume** — suspend the running ffmpeg subprocess and freeze segment progress; long pauses (>24h) auto-cancel (v0.7.0 R6 / F5) |
| Cmd/Ctrl+T | **Auto-tune** — calibrate the selected profile against this input, save as `<profile>.tuned.yaml`, switch to it (v0.7.0 R4 / F7) |
| Cmd/Ctrl+Q | **Open QA report** — open the HTML report from the most recent run |
| Cmd/Ctrl+P | **Preflight** — re-run preflight checks against the current source |

### Settings screen

| Shortcut | Action |
|---|---|
| Cmd/Ctrl+, | **Open Settings** |

### Notifications (Settings → Post-job notifications)

The Settings screen carries a **Test** button per notifications channel
(v0.7.0 R5 / F4) that synthesises a `completed` event and dispatches it
via `core.notifications.dispatch` — handy for verifying webhook URLs +
SMTP creds without running a full encode.

## Where data lives

Resolved via `QStandardPaths.AppConfigLocation` + `CacheLocation` (v0.7.0
R1 / E3) so paths follow each OS's convention. A migration helper copies
any legacy `~/.config/yt_uniquifier/` content on first launch.

| Path (typical) | OS conv. | What |
|---|---|---|
| `<CONFIG_DIR>/state.json`            | macOS `~/Library/Preferences/yt-uniquifier`, Linux `~/.config/yt-uniquifier`, Windows `%APPDATA%\yt-uniquifier` | Theme, recents, default profile/encoder, notifications config |
| `<CONFIG_DIR>/history.json`          | same as above | Run history (≤100 entries) |
| `<CONFIG_DIR>/crash.log`             | same as above | Global excepthook trace log (100 KiB rotation, v0.7.0 R2 / E6) |
| `<CACHE_DIR>/encoders.json`          | macOS `~/Library/Caches/yt-uniquifier`, Linux `~/.cache/yt-uniquifier`, Windows `%LOCALAPPDATA%\yt-uniquifier\Cache` | Encoder detection cache (resettable from Settings) |
| `<CACHE_DIR>/work/<plan_hash>/`      | same as above | Run work_dir (segments, state.json, resume marker) |
| `<CACHE_DIR>/keyframes/`             | same as above | Keyframe scan cache (30-day TTL) |
| `<CACHE_DIR>/corpus/index.json`      | same as above | Corpus fingerprint index |
| `<CACHE_DIR>/batch/<plan_hash>/`     | same as above | Batch worker scratch |
| `<CACHE_DIR>/worker/<plan_hash>/`    | same as above | Queue worker scratch |

## Packaging

A starter `pyinstaller/yt-uniq-gui.spec` is shipped. On macOS:

```bash
pip install pyinstaller
pyinstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm
open dist/yt-uniq-gui.app    # unsigned: right-click → Open first time
```

On Windows / Linux PyInstaller can also produce a one-file
distribution. If PyInstaller fails on your platform, the fallback is
always `pipx install 'yt-uniquifier[gui]'` which works everywhere.

## Troubleshooting

**Q: PyQt6 not installing on my Linux.**
A: Some distros need `apt install libxcb-cursor0 libgl1` before
PyQt6's binary wheel works.

**Q: QA Viewer shows a label instead of the embedded report.**
A: Either `PyQt6-WebEngine` isn't installed (`pip install
'yt-uniquifier[gui]'`) or you're running headless (offscreen Qt forces
the label fallback because the embedded browser crashes there).

**Q: Encoders look wrong / unavailable.**
A: Settings → Reset encoder cache. Next launch re-probes.
