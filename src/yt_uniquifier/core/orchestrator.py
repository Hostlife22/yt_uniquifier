"""End-to-end orchestration: probe → preflight → segment → resume → concat → metadata.

`run_full` is the single entry point that CLI (cmd_run, cmd_batch) and the
GUI Worker all call. It accepts an event callback so any UI can stream
progress events (RunEvent + custom phase markers).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.errors import PipelineError, PreflightFailure
from yt_uniquifier.core.metadata import build_metadata_args
from yt_uniquifier.core.models import Plan, Profile
from yt_uniquifier.core.notifications import (
    NotificationConfig,
    NotificationContext,
)
from yt_uniquifier.core.notifications import (
    dispatch as dispatch_notifications,
)
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import PreflightFinding, has_fail, preflight
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.runner import CancelToken, PauseToken, RunEvent
from yt_uniquifier.core.seed_resolver import resolve_run_seed
from yt_uniquifier.core.segmenter import (
    concat_segments,
    plan_segments,
    process_main_audio,
    process_video_segments_parallel,
)


@dataclass(frozen=True)
class RunOptions:
    work_dir: Path
    output: Path
    encoder_override: str | None = None
    title_template: str | None = None
    target_segment_sec: float = 600.0
    keep_segments: bool = False
    enforce_preflight: bool = True
    # If True, ignore an existing state.json's run_seed and force a fresh
    # seed (per the profile's seed_strategy). Used by `yt-uniq run --new-variant`.
    force_new_variant: bool = False
    # >1 enables parallel segment encoding on CPU (libx264/libx265 only).
    # GPU encoders silently fall back to sequential (single VRAM context).
    workers: int = 1
    # v0.4.3 — second-pass libx264 re-encode of the final output to strip
    # NVENC/QSV/AMF/VideoToolbox bitstream signatures. Adds ~30-60 min
    # wall time + ~3 VMAF points drop on long sources. No-op for
    # libx264-source runs. Refused on HDR/HEVC paths.
    sanitize_bitstream: bool = False
    # v0.7 R4 / F2 — live divergence sampling. After each re-encoded
    # segment, pull a few frames from both source[start..end] and the
    # encoded output, compute pHash similarity, and emit a
    # ``divergence_sample`` RunEvent. Drives the live-divergence GUI
    # indicator.
    #   "off"   — disabled (default; sampling costs ~50 ms per segment)
    #   "light" — sample 2 frames every 4th segment
    #   "full"  — sample 4 frames every segment
    sample_phash: Literal["off", "light", "full"] = "off"
    # v0.7 R5 / F4 — post-job notification config (Discord / Slack /
    # Telegram / generic webhook + optional SMTP). None = silent.
    # Dispatch is best-effort and never propagates errors.
    notifications: NotificationConfig | None = None

    def __post_init__(self) -> None:
        # Validate bounds at the public contract level. Without these:
        #   - target_segment_sec=0 made plan_segments spin (`span >= 0`
        #     is always true), producing tens of thousands of microseg
        #     ffmpeg forks.
        #   - workers=10_000_000 was clamped only deeper inside
        #     process_video_segments_parallel; surfacing the rule here
        #     gives callers a clear error early.
        if not (1.0 <= self.target_segment_sec <= 86400.0):
            raise ValueError(
                f"target_segment_sec must be in [1, 86400] seconds; "
                f"got {self.target_segment_sec}",
            )
        if not (1 <= self.workers <= 64):
            raise ValueError(
                f"workers must be in [1, 64]; got {self.workers}",
            )


@dataclass(frozen=True)
class RunSummary:
    output: Path
    plan: Plan
    segments_done: int
    preflight_findings: list[PreflightFinding]


def build_plan(input_path: Path, profile: Profile, encoder_override: str | None) -> Plan:
    source = probe_file(input_path)
    enc = pick_encoder(
        detect_encoders(),
        prefer=[encoder_override] if encoder_override else None,
        codec=profile.target_codec,
    )
    run_seed = resolve_run_seed(profile, source)
    return Plan(
        source=source,
        profile=profile,
        encoder=enc,
        plan_hash=compute_plan_hash(source, profile, enc),
        run_seed=run_seed,
    )


def run_full(
    plan: Plan,
    options: RunOptions,
    *,
    on_event: Callable[[RunEvent], None] | None = None,
    cancel_token: CancelToken | None = None,
    pause_token: PauseToken | None = None,
) -> RunSummary:
    """Process one input from probe to final mp4.

    Wraps the implementation in a try/except so v0.7 R5 / F4 post-job
    notifications fire on both the success return AND the uncaught-
    exception path — without forcing the impl to thread a dispatch
    call through each early-return branch.
    """
    emit = on_event or (lambda _e: None)
    try:
        summary = _run_full_impl(
            plan, options, emit, cancel_token, pause_token,
        )
    except BaseException as exc:
        _maybe_dispatch_notification(
            options, plan, "failed",
            extra_message=f"{type(exc).__name__}: {exc}",
            emit=emit,
        )
        raise
    _maybe_dispatch_notification(
        options, plan, "completed",
        summary=summary, emit=emit,
    )
    return summary


def _run_full_impl(
    plan: Plan,
    options: RunOptions,
    emit: Callable[[RunEvent], None],
    cancel_token: CancelToken | None,
    pause_token: PauseToken | None = None,
) -> RunSummary:
    findings = preflight(plan.source, plan, plan.encoder)
    if options.enforce_preflight and has_fail(findings):
        emit(RunEvent(kind="error", payload={"phase": "preflight",
                                             "findings": [f.model_dump() for f in findings]}))
        raise PreflightFailure(_format_findings(findings))
    emit(RunEvent(kind="log", payload={"phase": "preflight",
                                       "findings": [f.model_dump() for f in findings]}))

    # --new-variant: archive any existing state so we start fresh with the
    # newly-resolved run_seed (otherwise the existing 'done' segments would be
    # concat'd as-is and the new seed would be applied to nothing).
    options.work_dir.mkdir(parents=True, exist_ok=True)
    if options.force_new_variant:
        state_path = options.work_dir / "state.json"
        if state_path.exists():
            state_path.rename(state_path.with_suffix(
                f".json.stale-variant-{int(time.time())}"
            ))

    store = CheckpointStore(options.work_dir, plan)
    segments = store.init_or_resume(
        plan_segments(plan, target_size_sec=options.target_segment_sec)
    )

    # v0.7 R6 / F5 — pause observer thread. Watches pause_token state
    # transitions and (a) persists `paused_at` to state.json so a crash
    # mid-pause leaves an audit trail and (b) enforces the 24-hour
    # auto-cancel safety net so a forgotten pause never strands work.
    # The thread is daemon=True so process shutdown doesn't block on it.
    _pause_stop_event = _start_pause_observer(
        pause_token, cancel_token, store, emit,
    )

    # Idempotent re-run over a previously-completed work-dir
    # (HIGH-4 from 2026-05-30 v2 test report). If state.json says every
    # segment is done AND the final output exists, return early — the
    # work was already finished. If output is missing but state says done,
    # the segments were cleaned up after a successful concat; reset to
    # pending so the run re-processes from scratch.
    if (
        segments
        and all(s.status == "done" for s in segments)
        and not options.force_new_variant
    ):
        if options.output.exists() and options.output.stat().st_size > 0:
            emit(RunEvent(kind="log", payload={
                "phase": "resume",
                "message": "output already exists and all segments done — no-op",
                "output": str(options.output),
            }))
            return RunSummary(
                output=options.output,
                plan=plan,
                segments_done=len(segments),
                preflight_findings=findings,
            )
        # A3 (v0.5.5): output gone but state.json reports done →
        # segments were cleaned up after a successful concat. We need
        # per-segment recovery, not all-or-nothing reset. The previous
        # `any(... .exists())` check only fired when ALL segment files
        # were missing; under partial cleanup (e.g. NFS partial sync,
        # an interrupted cleanup, the user manually deleting a few
        # corrupted segments) the reset was skipped and concat then
        # failed with "Impossible to open seg_NNNN.mkv".
        missing = [
            s for s in segments
            if not (s.out_path and s.out_path.exists())
        ]
        if missing:
            for s in missing:
                store.mark(s.idx, "pending")
            segments = store.all_segments()
            emit(RunEvent(kind="log", payload={
                "phase": "resume",
                "message": (
                    f"state.json reports done but {len(missing)}/"
                    f"{len(segments)} segment files are missing — "
                    "re-encoding the missing ones"
                ),
                "missing_segments": [s.idx for s in missing],
            }))

    # Resume: if state.json carried a run_seed from an earlier run, reuse it
    # so the resumed encoding reproduces the same stochastic transforms.
    if not options.force_new_variant:
        stored_seed = store.stored_run_seed()
        if stored_seed is not None and stored_seed != plan.run_seed:
            plan = plan.model_copy(update={"run_seed": stored_seed})
            emit(RunEvent(kind="log", payload={
                "phase": "resume",
                "message": "restored run_seed from state.json",
                "run_seed": stored_seed,
            }))
    emit(RunEvent(kind="log", payload={"phase": "plan",
                                       "segments": len(segments)}))

    # Process pending video segments — sequentially (workers <= 1) or in
    # parallel on CPU encoders (libx264/libx265). GPU encoders silently
    # fall back to sequential inside process_video_segments_parallel.
    pending = [s for s in segments if s.status != "done"]
    if pending:
        # Re-check cancellation between each mark. A single check before
        # the loop allowed a window where cancel fired AFTER the check
        # but BEFORE the loop finished writing in_progress markers,
        # leaving N segments stuck at in_progress that resume would
        # never re-touch. With the check inside the loop, cancel
        # observes whichever marks have already been flushed and stops
        # cleanly.
        for seg in pending:
            if cancel_token and cancel_token.is_cancelled():
                raise PipelineError("cancelled by user")
            store.mark(seg.idx, "in_progress")

        # R4/F2 — segment lookup table for the divergence sampler. Built
        # once, indexed by idx so the per-segment hook is O(1).
        seg_by_idx = {s.idx: s for s in segments}

        def _on_segment_done(idx: int, src: Path | None, out: Path) -> None:
            # Verify the worker actually produced a non-empty output file
            # before recording it as done. If ffmpeg exited 0 but the file
            # was clobbered (or never written) we'd otherwise concat with
            # a `None` out_path and silently drop the segment.
            if not out.exists() or out.stat().st_size == 0:
                store.mark(idx, "failed")
                emit(RunEvent(kind="error", payload={
                    "phase": "segment",
                    "segment": idx,
                    "message": f"segment {idx} produced no output at {out}",
                }))
                raise PipelineError(
                    f"segment {idx} reported done but {out} is missing/empty"
                )
            store.mark(idx, "done", src_path=src, out_path=out)
            _maybe_emit_divergence(idx, out, seg_by_idx, options, plan, emit)

        try:
            process_video_segments_parallel(
                pending, plan, options.work_dir,
                workers=options.workers,
                on_event=lambda e: emit(RunEvent(
                    kind=e.kind,
                    payload={**e.payload, "phase": "segment"},
                )),
                cancel_token=cancel_token,
                pause_token=pause_token,
                on_segment_done=_on_segment_done,
            )
        except Exception:
            # A worker crashed; reset segments that never reached "done"
            # back to "failed" so resume sees a clean state. The re-mark
            # loop touches disk via store.mark → _flush; if _flush raises
            # (disk full, permissions) we must NOT propagate it in place
            # of the worker's original exception. Swallow flush errors
            # so the bare `raise` re-raises the original cause with its
            # full traceback intact (Python preserves __traceback__).
            try:
                for seg in store.all_segments():
                    if seg.status == "in_progress":
                        store.mark(seg.idx, "failed")
            except Exception as flush_exc:  # noqa: BLE001
                emit(RunEvent(kind="error", payload={
                    "phase": "checkpoint",
                    "message": f"failed to re-mark segments: {flush_exc}",
                }))
            raise

    # Main audio (cached via state.json).
    main_audio = store.get_main_audio()
    measurement = store.get_loudnorm()
    if main_audio is None or not main_audio.exists():
        main_audio, measurement = process_main_audio(
            plan, options.work_dir,
            loudnorm_measurement=measurement,
            on_event=lambda e: emit(RunEvent(
                kind=e.kind, payload={**e.payload, "phase": "main_audio"}
            )),
            cancel_token=cancel_token,
            pause_token=pause_token,
        )
        if measurement is not None:
            store.set_loudnorm(measurement)
        if main_audio is not None:
            store.set_main_audio(main_audio)

    # Concat + metadata. Defensive existence filter (A3 v0.5.5 belt-and-
    # suspenders): even after the resume-recovery block above, a worker
    # that crashed mid-finalise could leave a stale `out_path` set on
    # the Segment while the file itself is missing. Concat would then
    # fail with a cryptic "Impossible to open" — check upfront.
    final_segments = [
        s.out_path for s in store.all_segments()
        if s.out_path and s.out_path.exists()
    ]
    if not final_segments:
        raise PipelineError("no processed segments to concatenate")
    missing_finals = [
        s.idx for s in store.all_segments()
        if s.out_path and not s.out_path.exists()
    ]
    if missing_finals:
        raise PipelineError(
            f"cannot concat: {len(missing_finals)} segment file(s) gone "
            f"after re-encode: {missing_finals[:5]}{'…' if len(missing_finals) > 5 else ''}"
        )
    options.output.parent.mkdir(parents=True, exist_ok=True)
    concat_segments(
        final_segments,
        main_audio,
        options.output,
        build_metadata_args(plan, title_template=options.title_template),
        work_dir=options.work_dir,
        target_duration_sec=plan.source.duration_sec,
    )
    # v0.4.3 — optional bitstream sanitization (second-pass libx264).
    if options.sanitize_bitstream:
        from yt_uniquifier.core.sanitizer import (
            needs_sanitization,
            reject_for_hdr_or_hevc,
            sanitize_bitstream,
        )
        reject_for_hdr_or_hevc(plan.profile.keep_hdr, plan.encoder)
        if needs_sanitization(plan.encoder):
            emit(RunEvent(kind="log", payload={
                "phase": "sanitize",
                "message": f"re-encoding {plan.encoder.vendor} output via libx264",
            }))
            sanitize_bitstream(
                options.output, options.output, cancel_token=cancel_token,
            )
        else:
            emit(RunEvent(kind="log", payload={
                "phase": "sanitize",
                "message": "encoder is already libx264 — skipping (no-op)",
            }))

    emit(RunEvent(kind="done", payload={"output": str(options.output)}))

    if not options.keep_segments:
        _cleanup_artifacts(store, main_audio, emit)

    # Stop the pause observer thread cleanly before returning so it
    # doesn't outlive the RunSummary (would leak a daemon thread across
    # back-to-back batch entries).
    _pause_stop_event.set()

    return RunSummary(
        output=options.output,
        plan=plan,
        segments_done=len(final_segments),
        preflight_findings=findings,
    )


# v0.7 R5 / F4 — post-job notification dispatch.

def _maybe_dispatch_notification(
    options: RunOptions,
    plan: Plan,
    event_kind: Literal["completed", "failed"],
    *,
    summary: RunSummary | None = None,
    extra_message: str | None = None,
    emit: Callable[[RunEvent], None],
) -> None:
    """Build a NotificationContext and hand it to ``notifications.dispatch``.

    Routes any dispatch log lines back through ``emit`` as `log`
    RunEvents so the GUI's existing LogConsole shows what the
    webhook / email channels did. ``dispatch`` itself is no-raise,
    but the context build can still fail (e.g. profile name is an
    unprintable object); swallow that too to honour the "never
    propagate notification errors" invariant.
    """
    if options.notifications is None:
        return
    try:
        title = (
            f"yt-uniquifier: {plan.profile.name} — "
            f"{'completed' if event_kind == 'completed' else 'FAILED'}"
        )
        body_lines = [
            f"Source:  {plan.source.path.name}",
            f"Profile: {plan.profile.name}",
            f"Encoder: {plan.encoder.name} ({plan.encoder.vendor})",
        ]
        fields = {
            "plan_hash": plan.plan_hash[:12],
            "encoder":   f"{plan.encoder.name}/{plan.encoder.vendor}",
        }
        if summary is not None:
            body_lines.append(f"Output:  {summary.output}")
            body_lines.append(f"Segments done: {summary.segments_done}")
            fields["segments"] = str(summary.segments_done)
            fields["output"] = str(summary.output.name)
        if extra_message is not None:
            body_lines.append("")
            body_lines.append(f"Error: {extra_message}")
            fields["error"] = extra_message[:200]
        ctx = NotificationContext(
            event_kind=event_kind,
            title=title,
            body="\n".join(body_lines),
            fields=fields,
        )

        def _logger(msg: str, level: str) -> None:
            emit(RunEvent(kind="log", payload={
                "phase": "notifications",
                "level": level,
                "message": msg,
            }))

        dispatch_notifications(options.notifications, ctx, logger=_logger)
    except Exception as exc:  # noqa: BLE001 — never propagate
        emit(RunEvent(kind="log", payload={
            "phase": "notifications",
            "level": "warn",
            "message": f"notification dispatch error: {exc}",
        }))


# v0.7 R4 / F2 — exponential-moving-average half-life used by the
# running_phash field. Roughly: the most recent ~5 samples dominate,
# older samples decay. Match in DivergenceIndicator if the widget ever
# computes its own EMA from raw samples.
_DIVERGENCE_EMA_ALPHA = 0.25


def _maybe_emit_divergence(
    idx: int,
    out: Path,
    seg_by_idx: Mapping[int, object],
    options: RunOptions,
    plan: Plan,
    emit: Callable[[RunEvent], None],
) -> None:
    """Sample pHash similarity for one segment and emit a RunEvent.

    Cadence + frame count are driven by ``options.sample_phash``:

      * ``"off"``   — skip entirely (default)
      * ``"light"`` — every 4th segment, 2 frames per pair
      * ``"full"``  — every segment, 4 frames per pair

    Best-effort: any ffmpeg / I/O failure inside the sampler is
    swallowed so a transient frame-extract glitch never breaks the
    encode. The pipeline's primary signal (`done` segment) has
    already been recorded by the time this runs.
    """
    mode = options.sample_phash
    if mode == "off":
        return
    if mode == "light" and idx % 4 != 0:
        return

    n_frames = 4 if mode == "full" else 2
    seg = seg_by_idx.get(idx)
    if seg is None:
        return
    start_sec = getattr(seg, "start_sec", None)
    end_sec = getattr(seg, "end_sec", None)
    if not isinstance(start_sec, (int, float)) or not isinstance(end_sec, (int, float)):
        return
    span_sec = float(end_sec) - float(start_sec)
    if span_sec <= 0:
        return

    # Late import — qa.phash drags imagehash + PIL which we don't want
    # at module import time for non-sampling runs (CI subset matrix).
    from yt_uniquifier.core.qa.phash import compare_range_pair

    try:
        similarity = compare_range_pair(
            plan.source.path, out,
            source_start_sec=float(start_sec),
            span_sec=span_sec,
            n=n_frames,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort sampler
        emit(RunEvent(kind="log", payload={
            "phase": "divergence_sample",
            "segment": idx,
            "warning": f"divergence sampler failed: {exc}",
        }))
        return
    if similarity is None:
        return

    # Track an exponential moving average across segments to give the
    # GUI a stable "running" number that's not too jumpy on the last
    # sample. Stored on the function as a poor-man's closure cell so we
    # don't need to thread state through. Reset per run because the
    # orchestrator is a single-use object — but multiple runs in the
    # same process (tests, batch) would otherwise carry over: gate the
    # reset on plan.plan_hash.
    cache = _maybe_emit_divergence._ema  # type: ignore[attr-defined]
    cur = cache.get(plan.plan_hash)
    if cur is None:
        running = similarity
    else:
        running = (1 - _DIVERGENCE_EMA_ALPHA) * cur + _DIVERGENCE_EMA_ALPHA * similarity
    cache[plan.plan_hash] = running

    emit(RunEvent(kind="divergence_sample", payload={
        "segment": idx,
        "phash_similarity": float(similarity),
        "running_phash": float(running),
        "frames_sampled": n_frames,
    }))


_maybe_emit_divergence._ema = {}  # type: ignore[attr-defined]


def _cleanup_artifacts(
    store: CheckpointStore,
    main_audio: Path | None,
    emit: Callable[[RunEvent], None],
) -> None:
    """Best-effort segment + main-audio cleanup, surfacing persistent failures.

    `unlink(missing_ok=True)` only suppresses ENOENT; permissions and
    filesystem errors still raise. Without this wrapper they would
    propagate from the success path and mask a completed run as failed.
    """
    failures: list[str] = []
    for s in store.all_segments():
        for p in (s.src_path, s.out_path):
            if not p or not p.exists():
                continue
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"{p}: {exc}")
    if main_audio and main_audio.exists():
        try:
            main_audio.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{main_audio}: {exc}")
    if failures:
        emit(RunEvent(kind="error", payload={
            "phase": "cleanup",
            "message": f"could not delete {len(failures)} artifact(s)",
            "details": failures,
        }))


def _format_findings(findings: list[PreflightFinding]) -> str:
    fails = [f for f in findings if f.severity == "fail"]
    parts = [f"[{f.severity.upper()}] {f.code}: {f.message}" for f in fails]
    return "preflight failed:\n  " + "\n  ".join(parts)


# ---- v0.7 R6 / F5 — pause observer ----------------------------------------

def _start_pause_observer(
    pause_token: PauseToken | None,
    cancel_token: CancelToken | None,
    store: CheckpointStore,
    emit: Callable[[RunEvent], None],
) -> threading.Event:
    """Spawn the pause-state observer; return its stop event.

    Responsibilities:

    * Persist ``paused_at`` to ``state.json`` on pause and clear it on
      resume so a crash inside the pause window leaves a debuggable
      artefact.
    * Emit ``log`` RunEvents (``phase: paused | resumed | auto_cancel``)
      so the GUI's existing log console reflects the transition.
    * Enforce the 24-hour auto-cancel safety net via
      ``PauseToken.should_auto_cancel`` — a forgotten pause cancels the
      run rather than stranding work.

    The returned ``threading.Event`` is the caller's stop signal: set
    it at the end of ``_run_full_impl`` so the daemon exits before the
    RunSummary is returned.
    """
    stop = threading.Event()
    if pause_token is None:
        # No token: nothing to observe, but return a sentinel stop event
        # so the call-site is uniform.
        return stop

    def _observe() -> None:
        last_paused = False
        while not stop.is_set():
            now_paused = pause_token.is_paused()
            if now_paused and not last_paused:
                wall = pause_token.paused_at_wall()
                try:
                    store.set_paused_at(wall)
                except Exception as exc:  # noqa: BLE001 — observer is best-effort
                    emit(RunEvent(kind="error", payload={
                        "phase": "pause",
                        "message": f"could not persist paused_at: {exc}",
                    }))
                emit(RunEvent(kind="log", payload={"phase": "paused"}))
                last_paused = True
            elif not now_paused and last_paused:
                try:
                    store.set_paused_at(None)
                except Exception as exc:  # noqa: BLE001
                    emit(RunEvent(kind="error", payload={
                        "phase": "pause",
                        "message": f"could not clear paused_at: {exc}",
                    }))
                emit(RunEvent(kind="log", payload={"phase": "resumed"}))
                last_paused = False
            if now_paused and pause_token.should_auto_cancel():
                emit(RunEvent(kind="error", payload={
                    "phase": "pause",
                    "message": (
                        f"pause exceeded {PauseToken.AUTO_CANCEL_SEC} s — "
                        "auto-cancelling for safety"
                    ),
                }))
                if cancel_token is not None:
                    cancel_token.cancel()
                # Clear the marker so a successor run isn't confused.
                import contextlib
                with contextlib.suppress(Exception):
                    store.set_paused_at(None)
                return
            # 1 s poll — pause is a human-scale event; 1 s gives us
            # ~instant marker writes without burning CPU on idle runs.
            stop.wait(1.0)

    threading.Thread(
        target=_observe, daemon=True, name="pause-observer",
    ).start()
    return stop
