#!/usr/bin/env python3
"""Second-pass driver — covers the screens/scenarios left out of pass 1.

Scenarios:
    A. Cancel mid-run (verifies cancel path is clean)
    B. Queue drain (init + add + worker drain + stop)
    C. Profile Editor (select + save_as)
    D. History (smoke render)
    E. Corpus (refresh + table render)
    F. Validation (wizard renders without crash)
    G. Settings (theme toggle, action buttons no-crash)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    sys.stderr.write("driver refuses QT_QPA_PLATFORM=offscreen.\n")
    raise SystemExit(2)

VIDEO_LONG = REPO / "tests" / "fixtures" / ".gen" / "synth_long_5min.mp4"
VIDEO_SMALL = REPO / "tests" / "fixtures" / ".gen" / "clip_a.mp4"

OUT_ROOT = REPO / "out" / "manual_gui2" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT_ROOT / "driver.log"

QUEUE_ROOT = OUT_ROOT / "queue"
QUEUE_ROOT.mkdir(exist_ok=True)
QUEUE_OUT = OUT_ROOT / "queue_out"
QUEUE_OUT.mkdir(exist_ok=True)
PROFILES_TMP = OUT_ROOT / "profiles_tmp"
PROFILES_TMP.mkdir(exist_ok=True)


def _log(msg: str) -> None:
    line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _shot(window, name: str) -> Path:
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    time.sleep(0.2)
    QApplication.processEvents()
    pix = window.grab()
    target = OUT_ROOT / f"{name}.png"
    pix.save(str(target), "PNG")
    _log(f"shot → {target.name}")
    return target


def _switch(window, label: str) -> None:
    from PyQt6.QtWidgets import QApplication
    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    window.sidebar.setCurrentRow(order.index(label))
    QApplication.processEvents()
    time.sleep(0.4)
    QApplication.processEvents()
    _log(f"switched to {label}")


def _wait_until(predicate, timeout_sec: float, *, poll=0.1, label="condition"):
    from PyQt6.QtWidgets import QApplication
    start = time.monotonic()
    while not predicate() and time.monotonic() - start < timeout_sec:
        QApplication.processEvents()
        time.sleep(poll)
    elapsed = time.monotonic() - start
    ok = predicate()
    _log(f"wait[{label}] = {'OK' if ok else 'TIMEOUT'} in {elapsed:.1f}s")
    return ok


# ---------------- Scenario A: Cancel mid-run --------------------

def scenario_cancel(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Run")
    screen = window.screens["Run"]
    idx = screen.profile_combo.findText("medium")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.input_picker.set_path(VIDEO_LONG)
    out_mp4 = OUT_ROOT / "cancelled_output.mp4"
    screen.output_picker.set_path(out_mp4)
    QApplication.processEvents()
    _wait_until(lambda: screen.probe_worker is None, timeout_sec=20.0, label="probe")
    _shot(window, "A1_run_ready")

    screen.preflight_btn.click()
    _wait_until(lambda: screen.preflight_worker is None, timeout_sec=60.0, label="preflight")
    _shot(window, "A2_preflighted")

    _log("clicking ▶ Run then cancelling after 4s…")
    screen.run_btn.click()
    QApplication.processEvents()
    start = time.monotonic()
    while time.monotonic() - start < 4.0:
        QApplication.processEvents()
        time.sleep(0.2)
    _shot(window, "A3_mid_run")

    cancel_enabled = screen.cancel_btn.isEnabled()
    _log(f"cancel button enabled? {cancel_enabled}")
    screen.cancel_btn.click()
    _log("cancel clicked")
    QApplication.processEvents()

    # Wait for run_worker to be cleared (set by _on_cancelled)
    cancelled = _wait_until(
        lambda: screen.run_worker is None,
        timeout_sec=60.0, label="cancel",
    )
    _shot(window, "A4_cancelled")
    status = screen.status_label.text()
    _log(f"post-cancel status: '{status}'")
    # Cancel takes effect after current ffmpeg subprocess exits; verify
    # no orphan ffmpeg subprocess is still running our work_dir.
    import subprocess
    orphans = subprocess.run(
        ["pgrep", "-fl", "ffmpeg"], capture_output=True, text=True,
    ).stdout
    has_orphans = "yt_uniquifier" in orphans or "work_dir" in orphans
    _log(f"orphan ffmpeg processes referencing our work_dir? {has_orphans}")
    return {
        "cancelled_clean": cancelled,
        "status": status,
        "output_exists": out_mp4.exists(),
        "has_orphans": has_orphans,
    }


# ---------------- Scenario B: Queue drain -----------------------

def scenario_queue_drain(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Queue")
    screen = window.screens["Queue"]
    _shot(window, "B1_queue_blank")

    # Set queue root manually (bypass dialog)
    screen.queue_root = QUEUE_ROOT
    screen.root_label.setText(str(QUEUE_ROOT))
    screen.init_btn.setEnabled(True)
    screen.add_files_btn.setEnabled(True)
    screen.reset_stale_btn.setEnabled(True)
    QApplication.processEvents()

    # Init queue (creates buckets)
    _log("init queue…")
    screen.init_btn.click()
    _wait_until(lambda: screen.io_worker is None, timeout_sec=15.0, label="init")
    _shot(window, "B2_initialised")

    # Start status poller manually
    screen._start_status_poller()
    QApplication.processEvents()

    # Add clip_a via internal IO worker dispatch (bypass file dialog)
    _log("add clip_a to queue…")
    screen._run_io_worker("add", files=[VIDEO_SMALL], log_to_worker_log=True)
    _wait_until(lambda: screen.io_worker is None, timeout_sec=30.0, label="add")
    # Wait a poll cycle for status to update
    time.sleep(2.5)
    QApplication.processEvents()
    _shot(window, "B3_added")
    _log(f"queue stats after add: {screen.stats_label.text()}")

    # Configure worker side
    screen.out_dir = QUEUE_OUT
    screen.out_label.setText(str(QUEUE_OUT))
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.workers_spin.setValue(1)
    screen._refresh_start_btn()
    QApplication.processEvents()

    # Switch to Worker tab so the screenshot shows the action
    screen.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    _shot(window, "B4_worker_tab")

    if not screen.start_btn.isEnabled():
        _log("queue start button still disabled — aborting drain")
        return {"drained": False, "reason": "start button disabled"}

    _log("starting drain worker…")
    screen.start_btn.click()
    QApplication.processEvents()
    _shot(window, "B5_drain_started")

    # Wait for drain to finish (worker clears when done)
    start = time.monotonic()
    last_shot = start
    while screen.drain_worker is not None and time.monotonic() - start < 180:
        QApplication.processEvents()
        time.sleep(0.5)
        if time.monotonic() - last_shot > 15:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"B6_drain_progress_{elapsed:04d}s")
            _log(f"queue stats: {screen.stats_label.text()}")
            last_shot = time.monotonic()
    elapsed = time.monotonic() - start
    _shot(window, "B7_drain_final")
    _log(f"queue stats final: {screen.stats_label.text()}")

    outputs = sorted(p.name for p in QUEUE_OUT.iterdir() if p.suffix == ".mp4")
    _log(f"queue outputs: {outputs}")

    # Stop poller cleanly
    if screen.status_worker is not None:
        screen.status_worker.requestInterruption()
        screen.status_worker.quit()
        screen.status_worker.wait(2000)

    return {
        "drain_elapsed_sec": elapsed,
        "outputs": outputs,
        "final_stats": screen.stats_label.text(),
    }


# ---------------- Scenario C: Profile Editor --------------------

def scenario_profile_editor(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Profile Editor")
    screen = window.screens["Profile Editor"]
    _shot(window, "C1_editor_blank")

    # Pick cid_aware
    idx = screen.profile_combo.findText("cid_aware")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    QApplication.processEvents()
    time.sleep(0.4)
    _shot(window, "C2_editor_loaded")

    # Verify table populated
    row_count = screen.table.rowCount()
    _log(f"profile table rows: {row_count}")

    # Save-as directly (bypass dialog)
    target = PROFILES_TMP / "cid_aware_copy.yaml"
    from yt_uniquifier.core.profile_loader import dump_profile
    prof = screen._collect_profile()
    if prof is not None:
        dump_profile(prof, target)
        _log(f"profile saved to {target}")
    saved = target.exists()
    _shot(window, "C3_editor_saved")
    return {"row_count": row_count, "save_as_ok": saved}


# ---------------- D/E/F/G: smoke screens ------------------------

def scenario_history(window) -> dict:
    _switch(window, "History")
    screen = window.screens["History"]
    # History pulls from AppState.history; trigger refresh
    if hasattr(screen, "on_show"):
        screen.on_show()
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    time.sleep(0.4)
    _shot(window, "D1_history")
    rows = screen.table.rowCount()
    _log(f"history rows: {rows}")
    return {"rows": rows}


def scenario_corpus(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Corpus")
    screen = window.screens["Corpus"]
    _shot(window, "E1_corpus_initial")
    # Trigger refresh
    screen.refresh_btn.click()
    _wait_until(lambda: getattr(screen, "list_worker", None) is None,
                timeout_sec=15.0, label="corpus_list")
    _shot(window, "E2_corpus_refreshed")
    rows = screen.table.rowCount()
    _log(f"corpus rows: {rows}")
    return {"rows": rows}


def scenario_validation(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Validation")
    screen = window.screens["Validation"]
    _shot(window, "F1_validation_step1")
    # Switch through wizard steps via stack directly (next button may be
    # disabled until inputs are valid)
    screen.stack.setCurrentIndex(1)
    QApplication.processEvents()
    time.sleep(0.3)
    _shot(window, "F2_validation_step2")
    screen.stack.setCurrentIndex(2)
    QApplication.processEvents()
    time.sleep(0.3)
    _shot(window, "F3_validation_step3")
    screen.stack.setCurrentIndex(0)
    return {"steps_rendered": 3}


def scenario_settings(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Settings")
    screen = window.screens["Settings"]
    _shot(window, "G1_settings_initial")
    # Toggle theme combo
    current = screen.theme_combo.currentIndex()
    n = screen.theme_combo.count()
    if n > 1:
        screen.theme_combo.setCurrentIndex((current + 1) % n)
        QApplication.processEvents()
        time.sleep(0.3)
    _shot(window, "G2_settings_theme_toggled")
    # Click Save (just to exercise handler — no crash means pass)
    try:
        screen.save_btn.click()
        QApplication.processEvents()
    except Exception as exc:  # noqa: BLE001
        _log(f"settings save raised: {exc}")
    _shot(window, "G3_settings_saved")
    return {"theme_count": n}


# ---------------- main ------------------------------------------

def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.app_pyqt import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    QApplication.processEvents()
    time.sleep(0.8)

    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    win.screens = {label: win.stack.widget(i) for i, label in enumerate(order)}

    _log(f"out dir: {OUT_ROOT}")
    _shot(win, "00_startup")

    report: dict = {"out_dir": str(OUT_ROOT)}

    for key, fn in [
        ("cancel", scenario_cancel),
        ("queue_drain", scenario_queue_drain),
        ("profile_editor", scenario_profile_editor),
        ("history", scenario_history),
        ("corpus", scenario_corpus),
        ("validation", scenario_validation),
        ("settings", scenario_settings),
    ]:
        try:
            _log(f"=== SCENARIO {key} ===")
            report[key] = fn(win)
        except Exception as exc:  # noqa: BLE001
            _log(f"scenario {key} FAILED: {type(exc).__name__}: {exc}")
            import traceback
            _log(traceback.format_exc())
            report[f"{key}_error"] = str(exc)

    (OUT_ROOT / "report.json").write_text(
        json.dumps(report, indent=2, default=str),
    )
    _log(f"=== DONE — report at {OUT_ROOT / 'report.json'} ===")

    end_hold = time.monotonic() + 4.0
    while time.monotonic() < end_hold:
        QApplication.processEvents()
        time.sleep(0.1)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
