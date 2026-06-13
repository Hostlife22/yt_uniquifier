#!/usr/bin/env python3
"""Verify the two GUI fixes shipped in this session.

Fix #1 — Queue stop_when_empty: with the new checkbox checked, the
         drain worker MUST exit on its own after the queue empties
         (in the old behaviour it stayed daemon-polling forever).

Fix #2 — Run finalize phase labels: during the tail window the status
         line MUST contain "loudnorm" (or another non-fallback phase
         label) instead of only "finalizing (audio + mux)".
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
    raise SystemExit("offscreen not supported")

VIDEO_SMALL = REPO / "tests" / "fixtures" / ".gen" / "clip_a.mp4"
VIDEO_LONG = REPO / "tests" / "fixtures" / ".gen" / "synth_long_5min.mp4"

OUT_ROOT = REPO / "out" / "verify_fixes" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG = OUT_ROOT / "driver.log"
QUEUE_ROOT = OUT_ROOT / "queue"
QUEUE_ROOT.mkdir()
QUEUE_OUT = OUT_ROOT / "queue_out"
QUEUE_OUT.mkdir()


def _log(msg: str) -> None:
    line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _shot(window, name: str) -> None:
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    time.sleep(0.15)
    QApplication.processEvents()
    window.grab().save(str(OUT_ROOT / f"{name}.png"), "PNG")
    _log(f"shot → {name}.png")


def _switch(window, label: str) -> None:
    from PyQt6.QtWidgets import QApplication
    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    window.sidebar.setCurrentRow(order.index(label))
    QApplication.processEvents()
    time.sleep(0.3)
    _log(f"switched to {label}")


def _wait_until(predicate, timeout_sec: float, *, label: str) -> bool:
    from PyQt6.QtWidgets import QApplication
    start = time.monotonic()
    while not predicate() and time.monotonic() - start < timeout_sec:
        QApplication.processEvents()
        time.sleep(0.1)
    ok = predicate()
    elapsed = time.monotonic() - start
    _log(f"wait[{label}] = {'OK' if ok else 'TIMEOUT'} in {elapsed:.1f}s")
    return ok


# --------------------- Fix #1: Queue stop_when_empty ---------------

def verify_fix1_queue_stop_when_empty(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Queue")
    screen = window.screens["Queue"]
    _shot(window, "fix1_01_blank")

    # Set queue root + init (skip the modal info dialog by triggering
    # init synchronously via the helper rather than the button click).
    screen.queue_root = QUEUE_ROOT
    screen.root_label.setText(str(QUEUE_ROOT))
    screen.add_files_btn.setEnabled(True)

    # Direct init — avoid the QMessageBox modal that blocks io_worker
    # release in scripted runs.
    from yt_uniquifier.core.queue.leasing import init_queue
    init_queue(QUEUE_ROOT)
    screen._start_status_poller()
    QApplication.processEvents()
    time.sleep(1.5)
    _shot(window, "fix1_02_initialised")

    # Add file via the IO worker dispatch (this one does NOT show a modal)
    _log("adding clip_a to queue…")
    screen._run_io_worker("add", files=[VIDEO_SMALL], log_to_worker_log=True)
    _wait_until(lambda: screen.io_worker is None, timeout_sec=15.0, label="add")
    time.sleep(2.5)
    QApplication.processEvents()

    # Configure worker side
    screen.out_dir = QUEUE_OUT
    screen.out_label.setText(str(QUEUE_OUT))
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.workers_spin.setValue(1)

    # *** The fix under test: tick the new checkbox ***
    screen.stop_when_empty_check.setChecked(True)
    _log(f"stop_when_empty checked = {screen.stop_when_empty_check.isChecked()}")

    screen._refresh_start_btn()
    screen.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    _shot(window, "fix1_03_configured")

    if not screen.start_btn.isEnabled():
        _log("start button still disabled — abort")
        return {"passed": False, "reason": "start disabled"}

    _log("starting drain worker (one-shot mode)…")
    screen.start_btn.click()
    QApplication.processEvents()
    _shot(window, "fix1_04_started")

    start = time.monotonic()
    exited_cleanly = _wait_until(
        lambda: screen.drain_worker is None,
        timeout_sec=60.0, label="worker_exit_after_empty",
    )
    elapsed = time.monotonic() - start
    _shot(window, "fix1_05_after_exit")

    # Stop poller
    if screen.status_worker is not None:
        screen.status_worker.requestInterruption()
        screen.status_worker.quit()
        screen.status_worker.wait(2000)

    outputs = sorted(p.name for p in QUEUE_OUT.iterdir() if p.suffix == ".mp4")
    return {
        "passed": exited_cleanly,
        "exit_elapsed_sec": elapsed,
        "outputs": outputs,
        "stats": screen.stats_label.text(),
    }


# --------------------- Fix #2: Run finalize labels ----------------

def verify_fix2_run_finalize_labels(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Run")
    screen = window.screens["Run"]
    idx = screen.profile_combo.findText("medium")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.input_picker.set_path(VIDEO_LONG)
    out_mp4 = OUT_ROOT / "fix2_output.mp4"
    screen.output_picker.set_path(out_mp4)
    QApplication.processEvents()
    _wait_until(lambda: screen.probe_worker is None, timeout_sec=20.0, label="probe")

    screen.preflight_btn.click()
    _wait_until(lambda: screen.preflight_worker is None, timeout_sec=60.0, label="preflight")
    _shot(window, "fix2_01_preflighted")

    # Hook into status_label to capture every distinct label transition
    seen_labels: list[str] = []
    last = {"text": ""}

    _log("clicking ▶ Run…")
    screen.run_btn.click()
    QApplication.processEvents()

    start = time.monotonic()
    last_shot = start
    while screen.run_worker is not None and time.monotonic() - start < 600:
        QApplication.processEvents()
        time.sleep(0.2)
        cur = screen.status_label.text()
        if cur != last["text"]:
            seen_labels.append(cur)
            _log(f"status changed: {cur}")
            last["text"] = cur
        if time.monotonic() - last_shot > 20:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"fix2_02_progress_{elapsed:04d}s")
            last_shot = time.monotonic()

    _shot(window, "fix2_03_done")
    elapsed = time.monotonic() - start
    _log(f"run finished in {elapsed:.1f}s")

    # The fix is verified if we see ANY new label besides the fallback
    # during the tail window. main_audio is the only phase the
    # orchestrator stamps today; the new label is
    # "encoding audio (loudnorm 2-pass)".
    new_labels = [s for s in seen_labels if "loudnorm" in s or "concat + mux" in s
                  or "sanitizing bitstream" in s]
    fallback_only = all("finalizing (audio + mux)" in s for s in seen_labels if "—" in s
                        and "segment" not in s and "preparing" not in s)

    return {
        "passed": bool(new_labels),
        "elapsed_sec": elapsed,
        "status_transitions": seen_labels,
        "new_phase_labels_seen": new_labels,
        "fallback_only": fallback_only,
        "output_exists": out_mp4.exists(),
    }


# --------------------- main ----------------------------------------

def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.app_pyqt import MainWindow

    # _-prefix → ruff F841 happy; QApplication has to stay alive for
    # the Qt event loop, we just don't dereference the local.
    _app = QApplication.instance() or QApplication(sys.argv[:1])
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

    try:
        _log("=== FIX #1: queue stop_when_empty ===")
        report["fix1"] = verify_fix1_queue_stop_when_empty(win)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _log(f"fix1 FAILED: {exc}\n{traceback.format_exc()}")
        report["fix1_error"] = str(exc)

    try:
        _log("=== FIX #2: run finalize labels ===")
        report["fix2"] = verify_fix2_run_finalize_labels(win)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _log(f"fix2 FAILED: {exc}\n{traceback.format_exc()}")
        report["fix2_error"] = str(exc)

    (OUT_ROOT / "report.json").write_text(
        json.dumps(report, indent=2, default=str),
    )

    # Verdict
    f1 = report.get("fix1", {}).get("passed", False)
    f2 = report.get("fix2", {}).get("passed", False)
    _log(f"=== VERDICT: fix1={'PASS' if f1 else 'FAIL'}  fix2={'PASS' if f2 else 'FAIL'} ===")
    _log(f"report: {OUT_ROOT / 'report.json'}")

    end_hold = time.monotonic() + 3.0
    while time.monotonic() < end_hold:
        QApplication.processEvents()
        time.sleep(0.1)
    win.close()
    return 0 if (f1 and f2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
