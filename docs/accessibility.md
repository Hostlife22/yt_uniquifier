# Accessibility

`yt-uniquifier` targets **WCAG 2.1 Level AA** conformance for the desktop
GUI (`yt-uniq-gui`) and for the optional web UI (`yt-uniq-web`).
This page is the conformance statement, the manual-test checklist, and
the rationale for the few AA criteria where we apply a documented desktop
adaptation rather than the literal web wording.

The CLI (`yt-uniq …`) inherits the accessibility of the user's terminal
emulator and screen reader; it is not in scope for WCAG and is not
audited here.

## Conformance summary (v1.0.0)

| Principle      | Level | Status | How we verify it                                                                 |
|----------------|-------|--------|----------------------------------------------------------------------------------|
| Perceivable    | AA    | ✅      | `tests/gui/test_theme_contrast.py` — every painted (fg, bg) pair ≥ 4.5:1.        |
| Operable       | AA    | ✅      | `tests/gui/test_wcag_aa_compliance.py` — focus visible, keyboard-reachable.      |
| Understandable | AA    | ✅      | `tests/unit/test_gui_accessibility.py` — every interactive widget is named.      |
| Robust         | AA    | ✅      | Qt platform integration: PyQt6 exposes IA2 / AT-SPI / AX bridges out of the box. |

## Programmatic checks (CI gate)

These run on every push as part of `pytest`:

| Test file                                       | Asserts                                            | WCAG SC          |
|-------------------------------------------------|----------------------------------------------------|------------------|
| `tests/gui/test_theme_contrast.py`              | Every theme token (fg, bg) pair ≥ 4.5:1            | 1.4.3            |
| `tests/gui/test_wcag_aa_compliance.py`          | Theme QSS defines visible focus outline            | 2.4.7            |
| `tests/gui/test_wcag_aa_compliance.py`          | Every interactive widget has focusPolicy ≠ NoFocus | 2.1.1            |
| `tests/gui/test_wcag_aa_compliance.py`          | Every `QPushButton` sizeHint ≥ 20×20 px            | 2.5.5 (desktop)  |
| `tests/gui/test_wcag_aa_compliance.py`          | Every screen has a named subclass (role exposure)  | 1.3.1, 4.1.2     |
| `tests/unit/test_gui_accessibility.py`          | Every interactive widget has `accessibleName()`    | 4.1.2            |

The test suite is the contract: a regression that drops below AA fails CI
before the build can ship.

## Desktop adaptations

A few AA criteria are written for web pages and don't translate
literally to a Qt desktop app. We follow the
[WCAG2ICT](https://www.w3.org/WAI/standards-guidelines/wcag/wcag2ict/)
working-group guidance where the literal text doesn't apply.

| SC          | Web wording                            | Desktop interpretation we apply                                                            |
|-------------|----------------------------------------|--------------------------------------------------------------------------------------------|
| 2.5.5 AAA   | Targets ≥ 44×44 CSS-px                 | ≥ 20×20 device-px for `QPushButton` (matches Qt's stock per-platform sizeHint floor: Linux ≈ 22, Windows ≈ 20, macOS ≈ 22-24). Touch-mode users get the OS-level magnifier. |
| 1.4.4       | Text resize to 200 % without scrolling | Honoured via `QApplication.font()` + the OS DPI scale; `QT_FONT_DPI` and `QT_SCALE_FACTOR` are respected.       |
| 3.2.5       | Change of context on request only      | Theme switching is opt-in via the Settings screen; no auto-switch on focus.                |

## Keyboard reference

Every screen is fully operable with the keyboard. The complete chord
list:

| Chord                        | Action                                      |
|------------------------------|---------------------------------------------|
| `Tab` / `Shift+Tab`          | Move focus forward / backward.              |
| `Ctrl+1` … `Ctrl+9`          | Jump to sidebar items 1–9.                  |
| `Ctrl+0`                     | Jump to Settings (sidebar item 10).         |
| `Enter` / `Space`            | Activate the focused button or checkbox.    |
| `Esc`                        | Cancel an active run (RunScreen, Batch).    |
| `Ctrl+R`                     | Run the active job (RunScreen).             |
| `F1`                         | Open the in-app help (where available).     |

The sidebar is the keyboard's home base — if you ever lose focus, press
`Ctrl+1` to land on Run and Tab from there.

## Screen-reader manual test guide

CI verifies the static contract; the experience itself is best validated
with a real screen reader. We test against:

- **macOS**: VoiceOver (`Cmd+F5`).
- **Windows**: NVDA (free, [nvaccess.org](https://www.nvaccess.org/)).
- **Linux**: Orca + AT-SPI bridge (gnome-shell, KDE, XFCE all work).

### Smoke test (10 minutes)

1. Launch `yt-uniq-gui`. The screen reader should announce
   **"yt-uniquifier window, Main navigation list, Run"**.
2. Press `Ctrl+1` through `Ctrl+0`. Each step should announce the new
   screen's heading (e.g. *"Run screen"*, *"Settings screen"*).
3. From Run, press `Tab` repeatedly. Every focused control should
   announce both its **role** and its **accessible name** (e.g.
   *"Input file, edit"*, *"Browse, button"*).
4. Activate the Browse button with `Space`. The native file picker
   should open — its accessibility comes from the OS, not from us.
5. Switch to Settings (`Ctrl+0`), tab to the theme combo, change theme.
   The reader should announce the new theme name; no focus should be
   lost to a dialog the keyboard can't dismiss.

If any step is silent or announces *"unlabeled button"*, file a
`a11y` issue with the screen, the screen-reader name and version, and
the failing chord.

## Known limitations

- **Web UI charts**: the Plotly bundles ship their own ARIA wiring;
  we apply axis labels but do not yet expose a tabular text alternative
  for sighted-only data points. (Tracked as v1.1 work.)
- **CLI progress bars**: tqdm-style ticks update faster than most
  screen readers' polling interval. Pass `--no-progress` to fall back
  to a single end-of-run summary line, which is fully readable.
- **Mac VoiceOver in Qt 6.7**: a known upstream bug
  ([QTBUG-118473](https://bugreports.qt.io/browse/QTBUG-118473))
  occasionally swallows the role announcement on `QPushButton`. The
  name still reads correctly; we will move to Qt 6.8 once PyQt6 ships
  wheels for it.

## Reporting an a11y issue

GitHub Issues with the **`a11y`** label go to the same triage queue as
security reports, with a 5-business-day acknowledgement target. Please
include:

- OS + version, screen reader + version, application version
  (`yt-uniq-gui --version`), and the installed PyQt6/Qt version.
- The exact screen + chord that failed.
- Whether the failure is in the *announcement* (silent, wrong text,
  wrong role) or in the *interaction* (unreachable control, lost focus,
  keyboard trap).

We treat any AA regression as a v1.0.x patch-release-blocker; AAA
improvements land in the next minor.
