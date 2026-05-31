#!/usr/bin/env python3
"""Drive the real PyQt6 GUI through every screen the user asked to verify.

Window is fully visible — the script issues programmatic clicks on the
same widgets a human would click. Each major step writes a PNG to the
output dir and appends a line to driver.log so the run is auditable.
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
    sys.stderr.write("driver refuses QT_QPA_PLATFORM=offscreen — needs a real window.\n")
    raise SystemExit(2)

VIDEO_LONG = REPO / "tests" / "fixtures" / ".gen" / "synth_long_5min.mp4"
VIDEO_SMALL = REPO / "tests" / "fixtures" / ".gen" / "clip_a.mp4"
VIDEO_B = REPO / "tests" / "fixtures" / ".gen" / "clip_b.mp4"

OUT_ROOT = REPO / "out" / "manual_gui" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT_ROOT / "driver.log"
WORK_DIR = OUT_ROOT / "work"
WORK_DIR.mkdir(exist_ok=True)
QUEUE_ROOT = OUT_ROOT / "queue"
QUEUE_ROOT.mkdir(exist_ok=True)
BATCH_OUT = OUT_ROOT / "batch_out"
BATCH_OUT.mkdir(exist_ok=True)
BATCH_IN = OUT_ROOT / "batch_in"
BATCH_IN.mkdir(exist_ok=True)
# Symlink the two small clips so we have a proper input dir for Batch.
for src in (VIDEO_SMALL, VIDEO_B):
    link = BATCH_IN / src.name
    if not link.exists():
        link.symlink_to(src)


def _log(msg: str) -> None:
    line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _shot(window, name: str) -> Path:
    """Grab the window pixmap to <out>/<name>.png."""
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
    """Switch sidebar to a labelled screen."""
    from PyQt6.QtWidgets import QApplication
    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    window.sidebar.setCurrentRow(order.index(label))
    QApplication.processEvents()
    time.sleep(0.4)
    QApplication.processEvents()
    _log(f"switched to {label}")


def _wait_signal(signal, timeout_sec: float, *, poll=0.1):
    """Spin Qt event loop until `signal` fires or timeout."""
    from PyQt6.QtWidgets import QApplication
    fired = {"value": None, "done": False}

    def _slot(*args, **kwargs):
        fired["value"] = args
        fired["done"] = True

    signal.connect(_slot)
    start = time.monotonic()
    while not fired["done"] and time.monotonic() - start < timeout_sec:
        QApplication.processEvents()
        time.sleep(poll)
    try:
        signal.disconnect(_slot)
    except (TypeError, RuntimeError):
        pass
    return fired["done"], fired["value"], time.monotonic() - start


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


# ------------------------------------------------------------------
# Scenarios

def scenario_run(window) -> dict:
    """Probe + preflight + full Run + QA on synth_long_5min."""
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Run")
    screen = window.screens["Run"]
    _shot(window, "01_run_blank")

    # Pick profile
    idx = screen.profile_combo.findText("medium")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    _log(f"profile selected: {screen.profile_combo.currentText()}")

    # Load video
    screen.input_picker.set_path(VIDEO_LONG)
    QApplication.processEvents()
    time.sleep(0.5)
    out_mp4 = OUT_ROOT / "run_output.mp4"
    screen.output_picker.set_path(out_mp4)
    QApplication.processEvents()
    _shot(window, "02_run_loaded")

    # Probe runs automatically on input change. Wait until probe worker
    # is released (the screen drops the reference when probe completes).
    _wait_until(
        lambda: screen.probe_worker is None or "probed:" in screen.log.text.toPlainText(),
        timeout_sec=20.0, label="probe",
    )
    _shot(window, "03_run_probed")

    # Preflight
    _log("clicking Run preflight…")
    screen.preflight_btn.click()
    QApplication.processEvents()
    _wait_until(lambda: screen.preflight_worker is None, timeout_sec=60.0, label="preflight")
    _shot(window, "04_run_preflighted")

    # Run
    _log("clicking ▶ Run…")
    screen.run_btn.click()
    QApplication.processEvents()
    _shot(window, "05_run_started")

    # Take periodic shots during the run so progress is visible in artifacts.
    start = time.monotonic()
    last_shot = start
    finished = False
    failed = False
    while not finished and not failed and time.monotonic() - start < 1800:
        QApplication.processEvents()
        time.sleep(0.5)
        # Worker reference is cleared in _on_done / _on_failed / _on_cancelled
        if screen.run_worker is None:
            finished = True
        if time.monotonic() - last_shot > 30:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"06_run_progress_{elapsed:04d}s")
            _log(f"status: {screen.status_label.text()}")
            last_shot = time.monotonic()

    elapsed = time.monotonic() - start
    _shot(window, "07_run_final")
    status = screen.status_label.text()
    _log(f"run finished in {elapsed:.1f}s; status='{status}'")

    qa_path = screen.qa_html_path
    qa_ok = qa_path is not None and qa_path.exists()
    if qa_ok:
        _log(f"QA report present: {qa_path}")
    return {
        "elapsed_sec": elapsed,
        "status": status,
        "qa_html": str(qa_path) if qa_path else None,
        "qa_exists": qa_ok,
        "output_exists": out_mp4.exists(),
        "output_size": out_mp4.stat().st_size if out_mp4.exists() else 0,
    }


def scenario_qa_viewer(window, qa_html: str | None) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "QA Viewer")
    screen = window.screens["QA Viewer"]
    _shot(window, "10_qa_blank")
    if not qa_html or not Path(qa_html).exists():
        _log("QA Viewer: no QA HTML to load")
        return {"loaded": False}
    # Mirror what _pick_existing does without the file dialog
    p = Path(qa_html)
    screen.qa_html_path = p
    screen.existing_label.setText(str(p))
    screen.open_browser_btn.setEnabled(True)
    if hasattr(screen.viewer, "load"):
        from PyQt6.QtCore import QUrl
        screen.viewer.load(QUrl.fromLocalFile(str(p)))
    QApplication.processEvents()
    time.sleep(2.0)
    QApplication.processEvents()
    _shot(window, "11_qa_loaded")
    return {"loaded": True, "path": str(p)}


def scenario_batch(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Batch")
    screen = window.screens["Batch"]
    _shot(window, "20_batch_blank")
    # Set dirs programmatically (skip file dialogs)
    screen.input_dir = BATCH_IN
    screen.input_label.setText(str(BATCH_IN))
    screen.output_dir = BATCH_OUT
    screen.output_label.setText(str(BATCH_OUT))
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen._refresh_preview()
    screen._refresh_run_btn()
    QApplication.processEvents()
    _shot(window, "21_batch_loaded")
    if not screen.run_btn.isEnabled():
        _log("batch run button still disabled — aborting batch scenario")
        return {"started": False, "reason": "run button disabled"}
    _log("clicking ▶ Run batch…")
    screen.run_btn.click()
    QApplication.processEvents()
    _shot(window, "22_batch_started")
    start = time.monotonic()
    last_shot = start
    while screen.worker is not None and time.monotonic() - start < 900:
        QApplication.processEvents()
        time.sleep(0.5)
        if time.monotonic() - last_shot > 20:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"23_batch_progress_{elapsed:04d}s")
            last_shot = time.monotonic()
    elapsed = time.monotonic() - start
    _shot(window, "24_batch_final")
    rows = []
    for r in range(screen.table.rowCount()):
        cells = [screen.table.item(r, c).text() if screen.table.item(r, c) else ""
                 for c in range(screen.table.columnCount())]
        rows.append(cells)
    _log(f"batch finished in {elapsed:.1f}s with {len(rows)} rows")
    return {
        "elapsed_sec": elapsed,
        "rows": rows,
        "outputs": sorted(p.name for p in BATCH_OUT.iterdir() if p.suffix == ".mp4"),
    }


def scenario_calibrate(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Calibrate")
    screen = window.screens["Calibrate"]
    _shot(window, "30_calib_blank")
    # Use smallest clip and lowest iterations for time budget
    screen.input_picker.set_path(VIDEO_SMALL)
    QApplication.processEvents()
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.iter_spin.setValue(2)
    screen.clip_spin.setValue(10)
    screen.target_spin.setValue(0.5)
    QApplication.processEvents()
    _shot(window, "31_calib_loaded")
    if not screen.run_btn.isEnabled():
        _log("calibrate run button still disabled — aborting")
        return {"started": False}
    _log("clicking ▶ Calibrate…")
    screen.run_btn.click()
    QApplication.processEvents()
    _shot(window, "32_calib_started")
    start = time.monotonic()
    last_shot = start
    while screen.worker is not None and time.monotonic() - start < 900:
        QApplication.processEvents()
        time.sleep(0.5)
        if time.monotonic() - last_shot > 30:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"33_calib_progress_{elapsed:04d}s")
            last_shot = time.monotonic()
    elapsed = time.monotonic() - start
    _shot(window, "34_calib_final")
    _log(f"calibrate finished in {elapsed:.1f}s; tuned={screen.tuned_profile is not None}")
    return {
        "elapsed_sec": elapsed,
        "tuned_profile_present": screen.tuned_profile is not None,
    }


def scenario_queue(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Queue")
    screen = window.screens["Queue"]
    _shot(window, "40_queue_blank")
    screen.queue_root = QUEUE_ROOT
    screen.root_label.setText(str(QUEUE_ROOT))
    screen.init_btn.setEnabled(True)
    screen.add_files_btn.setEnabled(True)
    QApplication.processEvents()
    _log("clicking Init queue here…")
    screen.init_btn.click()
    QApplication.processEvents()
    # Give the status worker a moment to poll
    time.sleep(2.0)
    QApplication.processEvents()
    _shot(window, "41_queue_initialised")
    # Add small clip directly via the io worker action
    screen.out_dir = BATCH_OUT
    screen.out_label.setText(str(BATCH_OUT))
    QApplication.processEvents()
    # Best-effort: call internal add-files path by bypassing file dialog.
    # We don't actually drain — that needs a worker subprocess and is
    # slow. Just verify the dashboard renders.
    stats = screen.stats_label.text()
    _log(f"queue stats: {stats}")
    _shot(window, "42_queue_ready")
    # Stop polling so we don't leak QThreads when the app closes
    if screen.status_worker is not None:
        screen.status_worker.requestInterruption()
        screen.status_worker.quit()
        screen.status_worker.wait(2000)
    return {"queue_root": str(QUEUE_ROOT), "stats": stats}


# ------------------------------------------------------------------

def main() -> int:
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.app_pyqt import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    QApplication.processEvents()
    time.sleep(0.8)

    # Build a dict of screen instances by label so scenarios can grab them.
    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    stack = win.stack
    win.screens = {label: stack.widget(i) for i, label in enumerate(order)}

    _log(f"out dir: {OUT_ROOT}")
    _log(f"video long: {VIDEO_LONG} (exists={VIDEO_LONG.exists()})")
    _log(f"video small: {VIDEO_SMALL} (exists={VIDEO_SMALL.exists()})")
    _shot(win, "00_startup")

    report: dict = {"out_dir": str(OUT_ROOT)}

    try:
        _log("=== SCENARIO 1: Run + QA ===")
        report["run"] = scenario_run(win)
    except Exception as exc:  # noqa: BLE001
        _log(f"scenario_run FAILED: {type(exc).__name__}: {exc}")
        report["run_error"] = str(exc)

    try:
        _log("=== SCENARIO 2: QA Viewer ===")
        report["qa_viewer"] = scenario_qa_viewer(win, report.get("run", {}).get("qa_html"))
    except Exception as exc:  # noqa: BLE001
        _log(f"scenario_qa_viewer FAILED: {type(exc).__name__}: {exc}")
        report["qa_error"] = str(exc)

    try:
        _log("=== SCENARIO 3: Batch ===")
        report["batch"] = scenario_batch(win)
    except Exception as exc:  # noqa: BLE001
        _log(f"scenario_batch FAILED: {type(exc).__name__}: {exc}")
        report["batch_error"] = str(exc)

    try:
        _log("=== SCENARIO 4: Calibrate ===")
        report["calibrate"] = scenario_calibrate(win)
    except Exception as exc:  # noqa: BLE001
        _log(f"scenario_calibrate FAILED: {type(exc).__name__}: {exc}")
        report["calibrate_error"] = str(exc)

    try:
        _log("=== SCENARIO 5: Queue ===")
        report["queue"] = scenario_queue(win)
    except Exception as exc:  # noqa: BLE001
        _log(f"scenario_queue FAILED: {type(exc).__name__}: {exc}")
        report["queue_error"] = str(exc)

    # Write final report
    (OUT_ROOT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    _log(f"=== DONE — report at {OUT_ROOT / 'report.json'} ===")

    # Hold the window open briefly so the user can inspect the final state
    end_hold = time.monotonic() + 6.0
    while time.monotonic() < end_hold:
        QApplication.processEvents()
        time.sleep(0.1)

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
