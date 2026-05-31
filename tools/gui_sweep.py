#!/usr/bin/env python3
"""Real-window sweep through all 10 GUI screens.

Companion to tests/visual/test_gui_screenshots.py — that suite runs under
QT_QPA_PLATFORM=offscreen (CI baseline). This script REFUSES to run
offscreen because the whole point is exercising the real platform plugin
(cocoa on macOS, xcb / wayland on Linux) where compositor / font / HiDPI
defects actually surface.

Outputs to out/gui_sweep/<timestamp>/:
  - <screen>.png for each of 10 screens
  - qt.log                 captured Qt messages (warnings, criticals)
  - report.md              checklist with file links + smoke-worker status

Usage:
    .venv/bin/python tools/gui_sweep.py
    .venv/bin/python tools/gui_sweep.py --out /tmp/sweep --smoke-worker
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

# Same ordering as tests/visual/test_gui_screenshots.py — keep them in sync.
SCREENS: dict[str, int] = {
    "Run":            0,
    "Batch":          1,
    "Calibrate":      2,
    "QA Viewer":      3,
    "Profile Editor": 4,
    "History":        5,
    "Corpus":         6,
    "Queue":          7,
    "Validation":     8,
    "Settings":       9,
}

WINDOW_SIZE = (1280, 800)
SETTLE_MS = 250


def _slug(label: str) -> str:
    return label.lower().replace(" ", "_")


def _ensure_real_platform() -> None:
    plat = os.environ.get("QT_QPA_PLATFORM", "")
    if plat == "offscreen":
        sys.stderr.write(
            "gui_sweep refuses QT_QPA_PLATFORM=offscreen — the whole "
            "point of this harness is exercising the real platform plugin. "
            "Use tests/visual/test_gui_screenshots.py for the offscreen "
            "baseline.\n"
        )
        raise SystemExit(2)


def _install_qt_log_capture(log_path: Path) -> list[str]:
    """Pipe qInstallMessageHandler output into a list + file."""
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    captured: list[str] = []
    fh = log_path.open("w", encoding="utf-8")

    def handler(mode: QtMsgType, _ctx: object, msg: str) -> None:
        kind = {
            QtMsgType.QtDebugMsg: "DEBUG",
            QtMsgType.QtInfoMsg: "INFO",
            QtMsgType.QtWarningMsg: "WARN",
            QtMsgType.QtCriticalMsg: "CRIT",
            QtMsgType.QtFatalMsg: "FATAL",
        }.get(mode, str(mode))
        line = f"[{kind}] {msg}"
        captured.append(line)
        fh.write(line + "\n")
        fh.flush()

    qInstallMessageHandler(handler)
    return captured


def _capture_screen(window: object, label: str, out_dir: Path) -> Path:
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication

    idx = SCREENS[label]
    window.sidebar.setCurrentRow(idx)  # type: ignore[attr-defined]
    app = QApplication.instance()
    assert app is not None
    app.processEvents()
    time.sleep(SETTLE_MS / 1000.0)
    app.processEvents()

    pix = window.grab().scaled(QSize(*WINDOW_SIZE))  # type: ignore[attr-defined]
    target = out_dir / f"{_slug(label)}.png"
    pix.save(str(target), "PNG")
    return target


def _run_smoke_worker(timeout_sec: float = 60.0) -> tuple[bool, str]:
    """Fire a tiny RunWorker against clip_a.mp4 × soft.yaml and wait.

    Returns (ok, message). Best-effort: missing fixture / missing profile
    is reported as a skip, not a failure.
    """
    fixture = Path("tests/fixtures/.gen/clip_a.mp4")
    profile = Path("src/yt_uniquifier/profiles/soft.yaml")
    if not fixture.exists():
        return False, f"skip: fixture {fixture} not generated (run corpus gen)"
    if not profile.exists():
        return False, f"skip: profile {profile} missing"
    try:
        from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
        from yt_uniquifier.core.profile_loader import load_profile
    except ImportError as exc:
        return False, f"skip: import error {exc}"

    out_mp4 = Path("out/gui_sweep/_smoke_out.mp4")
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    prof = load_profile(profile)
    plan = build_plan(fixture, prof, "libx264")
    options = RunOptions(
        work_dir=Path(".gui_sweep_work") / plan.plan_hash,
        output=out_mp4,
        encoder_override="libx264",
        keep_segments=False,
        enforce_preflight=False,
        workers=1,
    )
    start = time.monotonic()
    try:
        run_full(plan, options, on_event=lambda _ev: None)
    except Exception as exc:  # noqa: BLE001 — surface in report, don't crash sweep
        return False, f"fail: {type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - start
    if elapsed > timeout_sec:
        return False, f"fail: ran {elapsed:.1f}s > {timeout_sec}s budget"
    return True, f"ok: produced {out_mp4} in {elapsed:.1f}s"


def _write_report(
    out_dir: Path,
    captured_pngs: dict[str, Path],
    qt_log_lines: list[str],
    smoke: tuple[bool, str] | None,
    started_at: _dt.datetime,
) -> Path:
    report = out_dir / "report.md"
    lines: list[str] = []
    lines.append("# GUI sweep report")
    lines.append("")
    lines.append(f"- Generated: {started_at.isoformat(timespec='seconds')}")
    lines.append(f"- Qt platform: `{os.environ.get('QT_QPA_PLATFORM') or '(default)'}`")
    lines.append(f"- Window size: {WINDOW_SIZE[0]}×{WINDOW_SIZE[1]}")
    lines.append(f"- Settle delay: {SETTLE_MS}ms per screen")
    lines.append("")
    lines.append("## Screens captured")
    lines.append("")
    lines.append("| # | Screen | Capture | Pass / Notes |")
    lines.append("|---:|---|---|---|")
    for i, label in enumerate(SCREENS):
        png = captured_pngs.get(label)
        link = f"![{label}]({png.name})" if png else "_(missing)_"
        lines.append(f"| {i} | {label} | {link} | _fill in manually_ |")
    lines.append("")
    lines.append("## Qt log")
    lines.append("")
    warns = [
        line for line in qt_log_lines
        if "[WARN]" in line or "[CRIT]" in line or "[FATAL]" in line
    ]
    if not qt_log_lines:
        lines.append("_(no Qt messages — clean)_")
    elif not warns:
        lines.append(f"_{len(qt_log_lines)} info/debug messages; no warnings or criticals._")
    else:
        lines.append(f"**{len(warns)} warning/critical message(s):**")
        lines.append("")
        lines.append("```")
        for line in warns[:50]:
            lines.append(line)
        if len(warns) > 50:
            lines.append(f"... +{len(warns) - 50} more in qt.log")
        lines.append("```")
    lines.append("")
    lines.append("## Smoke worker")
    lines.append("")
    if smoke is None:
        lines.append("_(skipped — pass --smoke-worker to enable)_")
    else:
        ok, msg = smoke
        status = "PASS" if ok else "FAIL/SKIP"
        lines.append(f"- Status: **{status}**")
        lines.append(f"- Detail: `{msg}`")
    lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", type=Path, default=Path("out/gui_sweep"),
        help="Output root; a timestamped subdir is created under it.",
    )
    ap.add_argument(
        "--smoke-worker", action="store_true",
        help=(
            "Also fire a tiny RunWorker on clip_a × soft to exercise the "
            "worker bus end-to-end (~30s). Off by default."
        ),
    )
    args = ap.parse_args()

    _ensure_real_platform()

    started_at = _dt.datetime.now()
    out_dir = args.out / started_at.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    qt_log_lines = _install_qt_log_capture(out_dir / "qt.log")

    # Imports deferred so --help works without PyQt6 installed.
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.app_pyqt import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
    win.resize(*WINDOW_SIZE)
    win.show()
    app.processEvents()
    time.sleep(SETTLE_MS / 1000.0)
    app.processEvents()

    captured: dict[str, Path] = {}
    for label in SCREENS:
        try:
            captured[label] = _capture_screen(win, label, out_dir)
            sys.stdout.write(f"  captured {label} → {captured[label].name}\n")
        except Exception as exc:  # noqa: BLE001 — keep sweeping other screens
            sys.stderr.write(f"  ERROR on {label}: {exc}\n")

    win.close()
    app.processEvents()

    smoke_result: tuple[bool, str] | None = None
    if args.smoke_worker:
        sys.stdout.write("  running smoke worker (this may take ~30s)...\n")
        smoke_result = _run_smoke_worker()

    report = _write_report(out_dir, captured, qt_log_lines, smoke_result, started_at)
    sys.stdout.write(f"\nSweep complete: {report}\n")
    sys.stdout.write(f"  {len(captured)}/{len(SCREENS)} screens captured\n")
    warn_count = sum(
        1 for line in qt_log_lines if "[WARN]" in line or "[CRIT]" in line
    )
    sys.stdout.write(f"  {warn_count} Qt warning/critical message(s)\n")
    if smoke_result is not None:
        ok, msg = smoke_result
        sys.stdout.write(f"  smoke worker: {'PASS' if ok else 'FAIL/SKIP'} — {msg}\n")
    return 0 if len(captured) == len(SCREENS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
