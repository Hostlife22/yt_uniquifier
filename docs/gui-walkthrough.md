# GUI walkthrough

A screen-by-screen tour of the PyQt6 desktop client. The screen
reference (event signals, worker wiring, packaging) lives in
[`gui.md`](gui.md); this page is the user-facing tour.

## Launching

```bash
pip install yt-uniquifier[gui]
yt-uniq-gui
```

The first launch shows a one-time **Local telemetry** dialog
(off-by-default; see [Telemetry](telemetry.md)). The main window
opens on the **Run** screen.

## Sidebar

Ten screens, navigable via Ctrl+1..Ctrl+0:

| #  | Screen          | Use                                                      |
|----|-----------------|----------------------------------------------------------|
| 1  | Run             | Drive one encode, watch live divergence sparkline        |
| 2  | Batch           | Folder-scan, queue, encode many                          |
| 3  | Calibrate       | Auto-tune profile params against a quality target        |
| 4  | QA Viewer       | Open any `<out>.qa.html` produced by a past run          |
| 5  | Profile Editor  | Edit YAML profiles inline + **Browse community…** dialog |
| 6  | History         | Past runs with status + open-in-finder + re-run          |
| 7  | Corpus          | Manage the local fingerprint corpus (R2 SQLite store)    |
| 8  | Queue           | Lease-based queue (shared-FS distributed batch)          |
| 9  | Validation      | Real-CID validation harness for regression               |
| 10 | Settings        | Theme, language, default profile, telemetry, webhooks    |

## Keyboard shortcuts (Run screen)

| Shortcut    | Action                  |
|-------------|-------------------------|
| `Ctrl+R`    | Run                     |
| `Ctrl+T`    | Auto-tune profile       |
| `Space`     | Pause / Resume          |
| `Esc`       | Cancel                  |
| `Ctrl+S`    | Save preferences        |
| `Ctrl+Q`    | Quit                    |
| `Ctrl+1..0` | Jump to sidebar screen  |

## Settings → Language (v0.9 R5)

Switching the language is hot — the translator re-installs and
the choice persists to `state.json`. Open screens keep their
already-painted strings; close-and-reopen, or restart the app,
for a full refresh. Coverage matrix and contributor guide at
[Localization](i18n.md).

## Accessibility (v1.0.0 R6)

Every screen meets **WCAG 2.1 AA**: visible 2-px focus outline
on every interactive control (dark + light themes), every widget
has a screen-reader-announced name + role, every button hits the
≥24×24 px target-size floor, and every painted foreground /
background pair clears the 4.5:1 contrast ratio. Full conformance
statement + screen-reader manual test guide:
[`docs/accessibility.md`](accessibility.md).

## Screenshots

Screenshots live under `docs/screenshots/`. A future revision
of this page will embed them inline alongside the screen
descriptions; the artwork is intentionally separate from the
docs commit so a UI cosmetic tweak does not need a docs round.

Video walkthroughs (one ~90-second clip per major screen) are
deferred to a v1.x docs sprint. They will land here as embedded
YouTube links once recorded.
