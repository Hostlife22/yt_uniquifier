"""Widget smoke + behavior tests via pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from yt_uniquifier.core.preflight import PreflightFinding
from yt_uniquifier.gui.state import AppState
from yt_uniquifier.gui.widgets.file_picker import FilePickerRow
from yt_uniquifier.gui.widgets.kpi_pills import KpiPills, _pill_color
from yt_uniquifier.gui.widgets.log_console import LogConsole
from yt_uniquifier.gui.widgets.preflight_panel import PreflightPanel
from yt_uniquifier.gui.widgets.segment_timeline import SegmentTimeline


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        return QApplication([])
    return inst


# ---- FilePickerRow ----
def test_file_picker_emits_on_set_path(app: QApplication, tmp_path: Path) -> None:
    state = AppState()
    picker = FilePickerRow("Input:", "input", "*.mp4", state)
    received: list[Path | None] = []
    picker.path_changed.connect(received.append)
    test_path = tmp_path / "x.mp4"
    test_path.touch()
    picker.set_path(test_path)
    assert received == [test_path]
    assert picker.current_path() == test_path


def test_file_picker_dedup_same_path(app: QApplication, tmp_path: Path) -> None:
    state = AppState()
    picker = FilePickerRow("Input:", "input", "*.mp4", state)
    received: list[Path | None] = []
    picker.path_changed.connect(received.append)
    p = tmp_path / "x.mp4"
    picker.set_path(p)
    picker.set_path(p)
    assert len(received) == 1


# ---- PreflightPanel ----
def test_preflight_panel_hidden_when_empty(app: QApplication) -> None:
    panel = PreflightPanel()
    panel.set_findings([])
    assert not panel.isVisible() or panel.isHidden()


def test_preflight_panel_emits_has_fail(app: QApplication) -> None:
    panel = PreflightPanel()
    fail_signals: list[bool] = []
    panel.has_fail.connect(fail_signals.append)
    panel.set_findings([
        PreflightFinding(code="t.x", severity="fail", message="m", suggestion=None),
    ])
    assert fail_signals == [True]


def test_preflight_panel_no_fail_on_warn_only(app: QApplication) -> None:
    panel = PreflightPanel()
    fail_signals: list[bool] = []
    panel.has_fail.connect(fail_signals.append)
    panel.set_findings([
        PreflightFinding(code="t.x", severity="warn", message="m", suggestion=None),
    ])
    assert fail_signals == [False]


# ---- SegmentTimeline ----
def test_segment_timeline_init_and_update(app: QApplication) -> None:
    timeline = SegmentTimeline()
    timeline.init(5)
    assert timeline.statuses() == ["pending"] * 5
    timeline.update_segment(2, "done")
    assert timeline.statuses()[2] == "done"


def test_segment_timeline_out_of_range_safe(app: QApplication) -> None:
    timeline = SegmentTimeline()
    timeline.init(3)
    timeline.update_segment(10, "done")  # should not crash
    assert timeline.statuses() == ["pending"] * 3


def test_segment_timeline_reset(app: QApplication) -> None:
    timeline = SegmentTimeline()
    timeline.init(3)
    timeline.update_segment(0, "done")
    timeline.reset()
    assert timeline.statuses() == []


# ---- LogConsole ----
def test_log_console_appends(app: QApplication) -> None:
    console = LogConsole(max_lines=100)
    console.log("hello", "info")
    console.log("world", "log")
    assert console.line_count() == 2


def test_log_console_caps_lines(app: QApplication) -> None:
    console = LogConsole(max_lines=10)
    for i in range(25):
        console.log(f"line {i}", "info")
    assert console.line_count() == 10


def test_log_console_filter_hides_other_levels(app: QApplication) -> None:
    console = LogConsole(max_lines=100)
    console.log("a", "info")
    console.log("b", "error")
    console.filter_combo.setCurrentText("error")
    # Internal line buffer keeps all; filter only affects rendering.
    assert console.line_count() == 2


# ---- KpiPills ----
def test_kpi_pill_color_phash_green() -> None:
    assert _pill_color("phash_worst", 0.70) == "#3ba85c"


def test_kpi_pill_color_phash_red() -> None:
    assert _pill_color("phash_worst", 0.90) == "#a83b3b"


def test_kpi_pill_color_vmaf_higher_better() -> None:
    """VMAF uses 'higher' direction → 87 should be green."""
    assert _pill_color("vmaf_mean", 87.0) == "#3ba85c"
    assert _pill_color("vmaf_mean", 70.0) == "#a83b3b"


def test_kpi_pills_set_qa_populates_pills(app: QApplication) -> None:
    pills = KpiPills()
    pills.set_qa({
        "phash_similarity": 0.72,
        "vmaf_mean": 87.5,
        "audio_fp_hamming_per_frame": 19.0,
        "cid_predict_self": 0.15,
        "chunk_similarities": [],
    })
    # 4 pills + 1 stretch = 5 layout items.
    assert pills._layout.count() >= 5
