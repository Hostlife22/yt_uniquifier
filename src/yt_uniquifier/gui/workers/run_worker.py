"""QThread wrapper around `orchestrator.run_full` — main encoding worker."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.orchestrator import RunOptions, RunSummary, run_full
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.core.runner import RunEvent
from yt_uniquifier.gui.workers.base import WorkerBase


class RunWorker(WorkerBase):
    """Forward RunEvents from orchestrator.run_full as Qt signals.

    Overrides finished_ok to emit (output_path, qa_html_path) tuple
    for direct screen consumption.
    """

    # Override base.finished_ok with a more specific signature.
    finished_ok = pyqtSignal(str, str)             # output_path, qa_html_path
    segment_progress = pyqtSignal(int, str)        # segment_idx, status

    def __init__(
        self,
        plan: Plan,
        options: RunOptions,
        *,
        run_qa: bool = True,
        fast_qa: bool = False,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.options = options
        self.run_qa = run_qa
        self.fast_qa = fast_qa
        self._total_us = max(int(plan.source.duration_sec * 1_000_000), 1)
        self._seg_us: dict[int, int] = {}

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            summary = run_full(
                self.plan,
                self.options,
                on_event=self._on_event,
                cancel_token=self.cancel_token,
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        qa_html: Path | None = None
        if self.run_qa:
            try:
                qa_html = self._build_qa(summary)
            except Exception as exc:
                self.log.emit(f"QA failed: {exc}")
                qa_html = None

        self.finished_ok.emit(
            str(self.options.output), str(qa_html) if qa_html else "",
        )

    def _on_event(self, ev: RunEvent) -> None:
        if ev.kind == "log":
            phase = ev.payload.get("phase", "")
            self.log.emit(f"[{phase}] {ev.payload}")
            return
        if ev.kind == "error":
            self.log.emit(f"[error] {ev.payload}")
            return
        if ev.kind != "progress":
            return

        seg = ev.payload.get("segment")
        out_us = self._extract_out_time_us(ev)
        if isinstance(seg, int):
            self._seg_us[seg] = out_us
            self.segment_progress.emit(seg, "in_progress")
        total = sum(self._seg_us.values())
        fraction = min(total / self._total_us, 1.0)
        msg = f"{int(fraction * 100)}% — segment {seg if seg is not None else '?'}"
        self.progress.emit(fraction, msg)

    @staticmethod
    def _extract_out_time_us(ev: RunEvent) -> int:
        raw_us = ev.payload.get("out_time_us")
        if isinstance(raw_us, str):
            try:
                return int(raw_us)
            except ValueError:
                pass
        raw_ms = ev.payload.get("out_time_ms")
        if isinstance(raw_ms, str):
            try:
                return int(raw_ms) * 1000
            except ValueError:
                pass
        return 0

    def _build_qa(self, summary: RunSummary) -> Path:
        report = build_report(
            self.plan.source.path,
            summary.output,
            samples=60 if self.fast_qa else 120,
            run_vmaf=not self.fast_qa,
        )
        json_path = summary.output.with_suffix(summary.output.suffix + ".qa.json")
        html_path = summary.output.with_suffix(summary.output.suffix + ".qa.html")
        write_json(report, json_path)
        render_html(report, self.plan, html_path)
        return html_path
