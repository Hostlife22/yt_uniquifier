#!/usr/bin/env python3
"""Verify the new progress bars on every screen that has a worker.

Each scenario captures:
- "before" PNG (screen rendered, bar at 0)
- 1+ "during" PNGs (bar partway)
- "after" PNG (bar at completion)
- numeric value progression captured into report.json
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

VIDEO_LONG = REPO / "tests" / "fixtures" / ".gen" / "synth_long_5min.mp4"
VIDEO_SMALL = REPO / "tests" / "fixtures" / ".gen" / "clip_a.mp4"
VIDEO_B = REPO / "tests" / "fixtures" / ".gen" / "clip_b.mp4"

OUT_ROOT = REPO / "out" / "verify_progress" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG = OUT_ROOT / "driver.log"

QUEUE_ROOT = OUT_ROOT / "queue"
QUEUE_ROOT.mkdir()
QUEUE_OUT = OUT_ROOT / "queue_out"
QUEUE_OUT.mkdir()
BATCH_IN = OUT_ROOT / "batch_in"
BATCH_IN.mkdir()
BATCH_OUT = OUT_ROOT / "batch_out"
BATCH_OUT.mkdir()
for src in (VIDEO_SMALL, VIDEO_B):
    link = BATCH_IN / src.name
    if not link.exists():
        link.symlink_to(src)


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


def _switch(window, label: str) -> None:
    from PyQt6.QtWidgets import QApplication
    order = ["Run", "Batch", "Calibrate", "QA Viewer", "Profile Editor",
             "History", "Corpus", "Queue", "Validation", "Settings"]
    window.sidebar.setCurrentRow(order.index(label))
    QApplication.processEvents()
    time.sleep(0.3)


def _wait_until(predicate, timeout_sec: float, *, label: str) -> bool:
    from PyQt6.QtWidgets import QApplication
    start = time.monotonic()
    while not predicate() and time.monotonic() - start < timeout_sec:
        QApplication.processEvents()
        time.sleep(0.1)
    elapsed = time.monotonic() - start
    ok = predicate()
    _log(f"wait[{label}] = {'OK' if ok else 'TIMEOUT'} in {elapsed:.1f}s")
    return ok


# --------- Run + audio progress bar ----------------------------------

def verify_run(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Run")
    screen = window.screens["Run"]
    _shot(window, "run_01_blank")
    main_bar = screen.progress_bar
    audio_bar = screen.audio_progress_bar
    init = {"main_value": main_bar.value(), "audio_visible": audio_bar.isVisible()}
    _log(f"Run initial: {init}")

    idx = screen.profile_combo.findText("medium")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.input_picker.set_path(VIDEO_LONG)
    out_mp4 = OUT_ROOT / "run_output.mp4"
    screen.output_picker.set_path(out_mp4)
    QApplication.processEvents()
    _wait_until(lambda: screen.probe_worker is None, timeout_sec=20.0, label="probe")
    screen.preflight_btn.click()
    _wait_until(lambda: screen.preflight_worker is None, timeout_sec=60.0, label="preflight")
    _shot(window, "run_02_preflighted")

    screen.run_btn.click()
    QApplication.processEvents()

    main_progression: list[int] = []
    audio_appeared_at: float | None = None
    audio_progression: list[int] = []
    start = time.monotonic()
    last_shot = start
    last_main = -1
    last_audio = -1
    while screen.run_worker is not None and time.monotonic() - start < 600:
        QApplication.processEvents()
        time.sleep(0.2)
        v = main_bar.value()
        if v != last_main:
            main_progression.append(v)
            last_main = v
        if audio_bar.isVisible():
            if audio_appeared_at is None:
                audio_appeared_at = time.monotonic() - start
                _log(f"audio bar appeared at {audio_appeared_at:.1f}s")
                _shot(window, f"run_03_audio_appeared_{int(audio_appeared_at)}s")
            av = audio_bar.value()
            if av != last_audio:
                audio_progression.append(av)
                last_audio = av
        if time.monotonic() - last_shot > 20:
            elapsed = int(time.monotonic() - start)
            _shot(window, f"run_04_progress_{elapsed:04d}s")
            last_shot = time.monotonic()

    elapsed = time.monotonic() - start
    _shot(window, "run_05_done")
    _log(f"Run done in {elapsed:.1f}s")
    _log(f"main bar progression points: {len(main_progression)}")
    _log(f"audio bar progression points: {len(audio_progression)}")
    return {
        "elapsed_sec": elapsed,
        "main_bar_final": main_bar.value(),
        "audio_bar_appeared_at_sec": audio_appeared_at,
        "audio_bar_final": audio_bar.value(),
        "audio_bar_visible_end": audio_bar.isVisible(),
        "main_progression_min_max": [min(main_progression or [0]),
                                      max(main_progression or [0])],
        "audio_progression_min_max": [min(audio_progression or [0]),
                                       max(audio_progression or [0])],
        "main_progression_unique_count": len(set(main_progression)),
        "audio_progression_unique_count": len(set(audio_progression)),
        "passed": (main_bar.value() == 1000
                   and audio_bar.value() > 0
                   and audio_appeared_at is not None
                   and len(set(audio_progression)) >= 2),
    }


# --------- Batch overall bar -----------------------------------------

def verify_batch(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Batch")
    screen = window.screens["Batch"]
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
    _shot(window, "batch_01_loaded")
    bar = screen.progress_bar
    initial = {"value": bar.value(), "max": bar.maximum()}
    _log(f"Batch initial bar: {initial}")
    if not screen.run_btn.isEnabled():
        return {"passed": False, "reason": "run disabled"}
    screen.run_btn.click()
    QApplication.processEvents()
    progression: list[int] = [bar.value()]
    last_v = bar.value()
    start = time.monotonic()
    while screen.worker is not None and time.monotonic() - start < 300:
        QApplication.processEvents()
        time.sleep(0.2)
        v = bar.value()
        if v != last_v:
            progression.append(v)
            last_v = v
            _shot(window, f"batch_02_step_{v}")
    _shot(window, "batch_03_done")
    return {
        "initial": initial,
        "final_value": bar.value(),
        "final_max": bar.maximum(),
        "progression": progression,
        "passed": bar.value() == bar.maximum() and len(progression) >= 2,
    }


# --------- Calibrate iteration bar -----------------------------------

def verify_calibrate(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Calibrate")
    screen = window.screens["Calibrate"]
    screen.input_picker.set_path(VIDEO_SMALL)
    QApplication.processEvents()
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.iter_spin.setValue(3)
    screen.clip_spin.setValue(10)
    screen.target_spin.setValue(0.5)
    QApplication.processEvents()
    _shot(window, "calib_01_loaded")
    bar = screen.progress_bar
    initial = {"value": bar.value(), "max": bar.maximum()}
    _log(f"Calibrate initial bar: {initial}")
    if not screen.run_btn.isEnabled():
        return {"passed": False, "reason": "run disabled"}
    screen.run_btn.click()
    QApplication.processEvents()
    progression: list[int] = [bar.value()]
    last_v = bar.value()
    start = time.monotonic()
    while screen.worker is not None and time.monotonic() - start < 600:
        QApplication.processEvents()
        time.sleep(0.2)
        v = bar.value()
        if v != last_v:
            progression.append(v)
            last_v = v
            _shot(window, f"calib_02_iter_{v}")
    _shot(window, "calib_03_done")
    return {
        "initial": initial,
        "final_value": bar.value(),
        "final_max": bar.maximum(),
        "progression": progression,
        "passed": len(progression) >= 2,
    }


# --------- Queue drain bar -------------------------------------------

def verify_queue(window) -> dict:
    from PyQt6.QtWidgets import QApplication
    _switch(window, "Queue")
    screen = window.screens["Queue"]
    screen.queue_root = QUEUE_ROOT
    screen.root_label.setText(str(QUEUE_ROOT))
    screen.add_files_btn.setEnabled(True)
    # Direct init (avoid the QMessageBox modal that hangs scripted runs)
    from yt_uniquifier.core.queue.leasing import init_queue
    init_queue(QUEUE_ROOT)
    screen._start_status_poller()
    QApplication.processEvents()
    time.sleep(1.5)
    _shot(window, "queue_01_init")
    bar = screen.progress_bar
    initial = {"value": bar.value(), "max": bar.maximum()}

    # Add both clips
    screen._run_io_worker("add", files=[VIDEO_SMALL, VIDEO_B], log_to_worker_log=True)
    _wait_until(lambda: screen.io_worker is None, timeout_sec=15.0, label="add")
    time.sleep(2.5)
    QApplication.processEvents()
    _shot(window, "queue_02_added")

    # Configure + start drain with stop_when_empty
    screen.out_dir = QUEUE_OUT
    screen.out_label.setText(str(QUEUE_OUT))
    idx = screen.profile_combo.findText("soft")
    if idx >= 0:
        screen.profile_combo.setCurrentIndex(idx)
    screen.workers_spin.setValue(1)
    screen.stop_when_empty_check.setChecked(True)
    screen._refresh_start_btn()
    screen.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    _shot(window, "queue_03_about_to_start")
    if not screen.start_btn.isEnabled():
        return {"passed": False, "reason": "start disabled"}
    screen.start_btn.click()
    QApplication.processEvents()

    progression: list[tuple[int, int]] = []
    last = (-1, -1)
    start = time.monotonic()
    while screen.drain_worker is not None and time.monotonic() - start < 240:
        QApplication.processEvents()
        time.sleep(0.5)
        cur = (bar.value(), bar.maximum())
        if cur != last:
            progression.append(cur)
            last = cur
            _shot(window, f"queue_04_step_{cur[0]}_of_{cur[1]}")
    _shot(window, "queue_05_done")
    # Stop poller
    if screen.status_worker is not None:
        screen.status_worker.requestInterruption()
        screen.status_worker.quit()
        screen.status_worker.wait(2000)
    return {
        "initial": initial,
        "final_value": bar.value(),
        "final_max": bar.maximum(),
        "progression": progression,
        "passed": bar.value() == bar.maximum() and bar.maximum() >= 2,
    }


# --------- Static visibility check on remaining screens -------------

def verify_static_bars(window) -> dict:
    results = {}
    for label in ("QA Viewer", "Validation", "Corpus"):
        _switch(window, label)
        screen = window.screens[label]
        # All three have a progress_bar OR gen_progress_bar attribute
        bar = getattr(screen, "progress_bar", None) or getattr(screen, "gen_progress_bar", None)
        present = bar is not None
        visible = bar.isVisible() if bar is not None else False
        _shot(window, f"static_{label.lower().replace(' ', '_')}")
        results[label] = {"bar_present": present, "visible_at_idle": visible}
        _log(f"{label}: bar_present={present}, visible_at_idle={visible}")
    return results


# --------- main -----------------------------------------------------

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

    for key, fn in [
        ("run", verify_run),
        ("batch", verify_batch),
        ("calibrate", verify_calibrate),
        ("queue", verify_queue),
        ("static", verify_static_bars),
    ]:
        try:
            _log(f"=== VERIFY {key} ===")
            report[key] = fn(win)
        except Exception as exc:  # noqa: BLE001
            import traceback
            _log(f"verify {key} FAILED: {exc}\n{traceback.format_exc()}")
            report[f"{key}_error"] = str(exc)

    (OUT_ROOT / "report.json").write_text(json.dumps(report, indent=2, default=str))

    summary = []
    for key in ("run", "batch", "calibrate", "queue"):
        passed = report.get(key, {}).get("passed", False)
        summary.append(f"{key}={'PASS' if passed else 'FAIL'}")
    _log(f"=== VERDICT: {' '.join(summary)} ===")
    _log(f"report: {OUT_ROOT / 'report.json'}")

    end_hold = time.monotonic() + 3.0
    while time.monotonic() < end_hold:
        QApplication.processEvents()
        time.sleep(0.1)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
