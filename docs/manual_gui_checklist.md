# Manual GUI smoke checklist

Run `scripts/manual_gui_smoke.sh` to launch the GUI with a generated
5-second sample clip. Walk every numbered step; record FAIL with the
screen + repro in the issue tracker.

Goal: catch anything `make test-gui` cannot — drag-and-drop, chart
rendering, hotkeys, dialog/file-pickers, theme switching, persistence
across restarts.

## 1. Run screen — golden path
- [ ] Drag the sample clip into the input picker (or use Browse…). Path appears.
- [ ] Pick an output path. The Run button enables.
- [ ] Click **Run preflight** — finding rows render in the panel (PASS or WARN, no FAIL on the sample).
- [ ] Click **▶ Run** — progress fraction advances, timeline shows segments green.
- [ ] On finish, status becomes "Done", KPI pills populate, Open QA report enables.
- [ ] Click Open QA report — system browser opens the `.qa.html`.

## 2. Run screen — cancel
- [ ] Start Run, click **Cancel** mid-run — status flips to "Cancelling…" then "Cancelled".
- [ ] Run button is re-enabled after cancellation.

## 3. Batch screen
- [ ] Pick a directory with 2+ mp4s. Table preview lists them.
- [ ] Change pattern to `*.mov` — preview updates / clears.
- [ ] Run batch, watch per-file status cycle: queued → running → done.
- [ ] Continue-on-error toggle visibly changes failure behavior when one file errors.

## 4. Calibrate screen
- [ ] Pick the sample as calibration input.
- [ ] Set iterations to 1 and click **▶ Calibrate** (short — under 1 min on tiny clip).
- [ ] After completion **Save tuned profile as…** is enabled.
- [ ] Save to a temp YAML and confirm file exists.

## 5. QA Viewer
- [ ] Switch to **Open existing** tab; pick the `.qa.json` from step 1.
- [ ] KPI tabs populate; charts render without empty space.
- [ ] **Open in browser** opens the HTML report.

## 6. Profile Editor
- [ ] Pick `cid_aware` from the dropdown — table populates with transforms.
- [ ] Edit one parameter, click **Save as…**, write to a new path.
- [ ] **Reload list** picks up the new profile.

## 7. History
- [ ] Verify the run from step 1 appears in the history table.
- [ ] Apply a filter — rows update live.
- [ ] **Clear all** prompts confirmation and empties the table.

## 8. Queue
- [ ] **Browse…** + **Init queue here** on an empty directory — buttons cycle from disabled to enabled.
- [ ] **Add files…** adds the sample. Stats label shows pending=1.
- [ ] **▶ Start worker** — sample drains; on completion stats show done=1.
- [ ] **Stop** during active drain halts cleanly.

## 9. Validation
- [ ] Step 1 → pick the sample + 2 variants + Generate; rows appear.
- [ ] Step 2 → record per-row CID outcome (pass/fail) and save CSV.
- [ ] Step 3 → Run correlation analysis; output box shows the report.

## 10. Settings + Corpus + theme switch
- [ ] Switch theme dark↔light — every visible screen re-skins immediately.
- [ ] Change recents/history caps and Save — re-open Settings to confirm persistence.
- [ ] Corpus → Add file → entry appears. Remove → row disappears.
- [ ] Close the app and re-launch — recents, history caps, theme survive restart.

## 11. Misc
- [ ] Resize window — no overlapping widgets, no clipped labels.
- [ ] On macOS: app menu bar shows Edit / View / Help with sensible items.
- [ ] No console errors on stderr after a normal session.
