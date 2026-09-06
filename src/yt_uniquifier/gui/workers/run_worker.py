"""QThread wrapper around `orchestrator.run_full` — main encoding worker."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.orchestrator import RunOptions, RunSummary, run_full
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.core.runner import PauseToken, RunEvent
from yt_uniquifier.gui.state import AppState, HistoryEntry
from yt_uniquifier.gui.workers.base import WorkerBase

# Throttle the LogConsole emit rate per phase so a multi-hour encode
# doesn't push tens of thousands of `[segment] {…}` lines into the
# QPlainTextEdit (each one triggers a relayout). Errors bypass the
# throttle. 0.5 s matches the 2 Hz progress cadence the UI already
# settles on, so the user does not perceive lost visibility.
_LOG_THROTTLE_SEC = 0.5


class RunWorker(WorkerBase):
    """Forward RunEvents from orchestrator.run_full as Qt signals.

    Overrides finished_ok to emit (output_path, qa_html_path) tuple
    for direct screen consumption.
    """

    # Override base.finished_ok with a more specific signature.
    finished_ok = pyqtSignal(str, str)             # output_path, qa_html_path
    segment_progress = pyqtSignal(int, str)        # segment_idx, status
    cancelled = pyqtSignal()                       # user-initiated cancel
    # Dedicated audio-finalize progress so the main bar can stay at 100%
    # while loudnorm 2-pass runs visibly on its own sub-bar. fraction is
    # audio_out_us / audio_duration_us; label carries phase context.
    audio_progress = pyqtSignal(float, str)        # fraction, label
    # v0.7 R4 / F2 — per-segment pHash sample. payload is the dict
    # from RunEvent(kind="divergence_sample"): segment, phash_similarity,
    # running_phash, frames_sampled.  Forwarded raw so screens can
    # consume the keys they care about without parsing a tuple.
    divergence_sample = pyqtSignal(dict)
    # v0.7 R6 / F5 — pause/resume state mirror. Emitted whenever
    # `request_pause()` / `request_resume()` is called so the Run screen
    # can flip the button label without polling the token directly.
    paused_changed = pyqtSignal(bool)
    # Marshal history writes back onto the GUI thread. `AppState` is a
    # QObject owned by the GUI thread; mutating its `_history` list and
    # emitting `history_changed` from the worker `run()` body would race
    # with the GUI's table updates and corrupt the on-disk JSON. Qt
    # delivers cross-thread signals via QueuedConnection by default.
    _history_request = pyqtSignal(object)          # HistoryEntry

    def __init__(
        self,
        plan: Plan,
        options: RunOptions,
        *,
        run_qa: bool = True,
        fast_qa: bool = False,
        state: AppState | None = None,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.options = options
        self.run_qa = run_qa
        self.fast_qa = fast_qa
        self.state = state
        self._total_us = max(int(plan.source.duration_sec * 1_000_000), 1)
        self._seg_us: dict[int, int] = {}
        # Audio finalize progress tracked separately from video segments
        # so the main bar can show "video complete" while the audio bar
        # advances through loudnorm. `_audio_us` is the latest seen
        # out_time_us from phase=main_audio events; if it goes backwards
        # (ffmpeg may run analysis pass then encode pass as separate
        # subprocess invocations) we bump `_audio_pass` to count progress
        # cumulatively across passes.
        self._audio_us: int = 0
        self._audio_pass: int = 1
        # ThreadPoolExecutor inside orchestrator.process_video_segments_parallel
        # invokes on_event concurrently from worker threads when workers > 1.
        # Without this lock the dict mutation + sum() composite is a data race
        # under PyPy and free-threaded CPython 3.13+; even under standard
        # CPython it produces transient progress overshoot.
        self._seg_us_lock = threading.Lock()
        # Throttle log emits per (phase) — last emit timestamp keyed by
        # phase name. Errors and 'log' events from never-before-seen
        # phases pass through immediately; subsequent log events in the
        # same phase wait for _LOG_THROTTLE_SEC.
        self._last_log_emit: dict[str, float] = {}
        # v0.7 R6 / F5 — owned by the worker (lifecycle matches the
        # encode run); the Run screen toggles via request_pause/resume
        # so the GUI never holds a reference to the token directly.
        self.pause_token = PauseToken()
        if self.state is not None:
            self._history_request.connect(self._on_history_request)

    def request_pause(self) -> None:
        if not self.pause_token.is_paused():
            self.pause_token.pause()
            self.paused_changed.emit(True)

    def request_resume(self) -> None:
        if self.pause_token.is_paused():
            self.pause_token.resume()
            self.paused_changed.emit(False)

    def is_paused(self) -> bool:
        return self.pause_token.is_paused()

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            summary = run_full(
                self.plan,
                self.options,
                on_event=self._on_event,
                cancel_token=self.cancel_token,
                pause_token=self.pause_token,
            )
        except Exception as exc:
            # Distinguish user cancel from genuine failure: the orchestrator
            # raises PipelineError("cancelled by user") when cancel_token
            # fires. Lumping that into "failed" misreports the history and
            # shows the user a misleading error banner.
            if self.cancel_token.is_cancelled():
                self._push_history("cancelled")
                self.cancelled.emit()
            else:
                self._push_history("failed")
                self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        qa_html: Path | None = None
        if self.run_qa:
            try:
                qa_html = self._build_qa(summary)
            except Exception as exc:
                self.log.emit(f"QA failed: {exc}")
                qa_html = None

        self._push_history("done", qa_html)
        self.finished_ok.emit(
            str(self.options.output), str(qa_html) if qa_html else "",
        )

    def _push_history(self, status: str, qa_html: Path | None = None) -> None:
        """v0.5.2 — record history entry on done / failed (best-effort).

        Called from the worker thread. The `HistoryEntry` is constructed
        here (cheap, pure-data) and emitted via `_history_request` — Qt
        delivers the signal to `_on_history_request` on the GUI thread,
        which is the only place that touches `AppState`.
        """
        if self.state is None:
            return
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            source_path=str(self.plan.source.path),
            profile_name=self.plan.profile.name,
            encoder_name=self.plan.encoder.name,
            output_path=str(self.options.output),
            qa_html_path=str(qa_html) if qa_html else None,
            plan_hash=self.plan.plan_hash,
            status=status,
        )
        self._history_request.emit(entry)

    def _on_history_request(self, entry: object) -> None:
        """GUI-thread slot — invoked via QueuedConnection from worker thread."""
        if self.state is None or not isinstance(entry, HistoryEntry):
            return
        try:
            self.state.push_history(entry)
        except OSError as exc:
            # Disk full / permissions / read-only FS. Surface so the user
            # knows the history entry was lost; do not crash the run.
            self.log.emit(f"history write failed: {exc}")

    def _on_event(self, ev: RunEvent) -> None:
        if ev.kind == "divergence_sample":
            # Pass the payload dict through verbatim. Qt copies it
            # across the queued connection, so the GUI thread receives
            # an independent dict — no shared-mutable-state worry from
            # the worker's perspective.
            self.divergence_sample.emit(dict(ev.payload))
            return
        if ev.kind == "log":
            phase = str(ev.payload.get("phase", ""))
            now = time.monotonic()
            last = self._last_log_emit.get(phase, 0.0)
            if now - last >= _LOG_THROTTLE_SEC:
                self._last_log_emit[phase] = now
                self.log.emit(f"[{phase}] {ev.payload}")
            return
        if ev.kind == "error":
            # Errors always pass through — never lose a failure signal
            # to throttling. They are also infrequent so flooding is
            # not a real concern.
            self.log.emit(f"[error] {ev.payload}")
            return
        if ev.kind != "progress":
            return

        seg = ev.payload.get("segment")
        phase = str(ev.payload.get("phase") or "")
        out_us = self._extract_out_time_us(ev)

        # ---- audio finalize phase: drive the secondary bar ----------
        # When the orchestrator stamps phase=main_audio, the event came
        # from process_main_audio's ffmpeg run (loudnorm + encode). We
        # bypass _seg_us so the main bar stays at its segments-derived
        # value and dedicate audio_progress to this stream.
        if phase == "main_audio":
            # Detect pass boundary: ffmpeg may invoke a measurement pass
            # then an encode pass as two separate subprocess runs, in
            # which case out_time_us resets to 0. Cumulate across passes
            # so the audio bar advances monotonically.
            if out_us < self._audio_us:
                self._audio_pass += 1
            self._audio_us = out_us
            cumulative_us = (self._audio_pass - 1) * self._total_us + out_us
            # Two-pass loudnorm total work ≈ 2 × duration; clamp at 1.0
            audio_fraction = min(cumulative_us / (2 * self._total_us), 1.0)
            pass_label = f"loudnorm pass {self._audio_pass}/2"
            self.audio_progress.emit(audio_fraction, pass_label)
            # Also keep the main status meaningful instead of stale.
            self.progress.emit(
                min(sum(self._seg_us.values()) / self._total_us, 1.0),
                f"100% — encoding audio ({pass_label})",
            )
            return

        with self._seg_us_lock:
            if isinstance(seg, int):
                self._seg_us[seg] = out_us
            total = sum(self._seg_us.values())
        if isinstance(seg, int):
            self.segment_progress.emit(seg, "in_progress")
        fraction = min(total / self._total_us, 1.0)
        # `seg` is an int while a video segment is encoding; None for
        # post-segment events that aren't main_audio (concat, sanitize).
        if isinstance(seg, int):
            label = f"segment {seg}"
        elif phase in {"concat", "mux"}:
            label = "concat + mux"
        elif phase == "sanitize":
            label = "sanitizing bitstream"
        elif fraction >= 0.99:
            label = "finalizing (audio + mux)"
        else:
            label = "preparing"
        msg = f"{int(fraction * 100)}% — {label}"
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
            plan=summary.plan,
            samples=60 if self.fast_qa else 120,
            run_vmaf=not self.fast_qa,
            run_registered=True,
            registration_target_segment_sec=self.options.target_segment_sec,
            # run_full already performed the mandatory complete A/V decode.
            verify_decode=False,
            decode_evidence=summary.decode_evidence,
        )
        json_path = summary.output.with_suffix(summary.output.suffix + ".qa.json")
        html_path = summary.output.with_suffix(summary.output.suffix + ".qa.html")
        write_json(report, json_path)
        render_html(report, summary.plan, html_path)
        return html_path
