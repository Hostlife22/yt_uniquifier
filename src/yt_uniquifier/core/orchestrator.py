"""End-to-end orchestration: probe → preflight → segment → resume → concat → metadata.

`run_full` is the single entry point that CLI (cmd_run, cmd_batch) and the
GUI Worker all call. It accepts an event callback so any UI can stream
progress events (RunEvent + custom phase markers).
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import datetime

from yt_uniquifier.core.auxiliary_streams import get_auxiliary_streams
from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.errors import PipelineError, PreflightFailure
from yt_uniquifier.core.logging_config import get_logger
from yt_uniquifier.core.media_validation import require_output_contract
from yt_uniquifier.core.metadata import build_metadata_args
from yt_uniquifier.core.models import Plan, Profile, Segment
from yt_uniquifier.core.notifications import (
    NotificationConfig,
    NotificationContext,
)
from yt_uniquifier.core.notifications import (
    dispatch as dispatch_notifications,
)
from yt_uniquifier.core.pipeline import compute_plan_hash, expected_output_duration
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
from yt_uniquifier.core.stream_policy import selected_audio_relative_indices
from yt_uniquifier.core.telemetry import TelemetryConfig
from yt_uniquifier.core.telemetry import record as record_telemetry


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
    # v0.9 R3 — opt-in local telemetry. None = disabled; passing an
    # explicit TelemetryConfig with enabled=True records one summary
    # event per run (completed or failed). Never network egress in
    # v0.9; see ``core/telemetry.py``.
    telemetry: TelemetryConfig | None = None
    # v1.1.0 Task 14: correlation ID for log / event / metric joins.
    # Auto-populated at orchestrator entry when None (uuid7 → time-
    # ordered, sortable, deduplicates well on log replay). Callers
    # may pre-bind their own ID (e.g. the web layer wants the same
    # value back in the HTTP response) by passing it explicitly.
    run_id: str | None = None
    # v1.3.0 Task 30 — operator attestation that they own / are
    # licensed to re-upload this content.  When True the watermark
    # guardrail in ``preflight()`` records the attestation as an
    # ``info`` finding and proceeds.  CLI flag:
    # ``--accept-watermark-risk``.
    accept_watermark_risk: bool = False
    # v1.3.0 Task 32 — run-level audit log path.  When set, every
    # completed / failed run appends a JSONL summary (run_id, plan_hash,
    # input_sha256, segments accounting).  ``None`` keeps the CLI default
    # quiet; operators can also set ``YT_UNIQ_AUDIT_LOG=/path`` in the
    # environment via ``audit.resolve_audit_log_path``.
    audit_log_path: Path | None = None
    # v1.3.0 Task 32 — operator label stamped on the run-level audit
    # entry.  Defaults to ``YT_UNIQ_AUDIT_PRINCIPAL`` env var.
    audit_principal: str | None = None

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
    # v1.1.0 Task 14: ensure a run_id exists exactly once per run and
    # stamp it onto every event the wrapped emit() sees. Time-ordered
    # so log/event/metric replays sort sensibly even on overlapping
    # batches; ``uuid7`` is supplied by the stdlib in 3.11+ via the
    # ``uuid.uuid8`` shim — fall back to ``uuid4`` if a host's libc
    # is too old to provide the monotonic clock that uuid7 needs.
    options = _ensure_run_id(options)
    run_id = options.run_id
    assert run_id is not None  # _ensure_run_id post-condition

    # v1.1.0 Task 13: bind run_id + plan_hash so every structured log
    # line emitted from the orchestrator and its callees carries the
    # correlation IDs without per-call boilerplate.
    log = get_logger(
        "yt_uniquifier.orchestrator",
        run_id=run_id,
        plan_hash=plan.plan_hash,
    )

    raw_emit = on_event or (lambda _e: None)

    def emit(event: RunEvent) -> None:
        # v1.1.0 Task 14: weave run_id into every payload so downstream
        # subscribers (web /api/run response, GUI run history,
        # /metrics counters) can correlate without a separate hook.
        if "run_id" not in event.payload:
            event = RunEvent(
                kind=event.kind,
                payload={**event.payload, "run_id": run_id},
            )
        raw_emit(event)

    log.info("run.started", input=str(plan.source.path), encoder=plan.encoder.name)
    start_ts = time.time()
    import datetime as _dt
    started_at_dt = _dt.datetime.now(tz=_dt.UTC)
    # v1.3.0 Task 34 — wrap the entire run in an OTel span when the
    # operator opts in via OTEL_EXPORTER_OTLP_ENDPOINT + [obs] extra.
    # No-op otherwise.  Using contextlib.ExitStack so the existing
    # try/except below stays unchanged; the span's __exit__ runs on
    # both the raise and the return path.
    import contextlib as _contextlib

    from yt_uniquifier.core import tracing as _tracing
    _span_stack = _contextlib.ExitStack()
    _span_stack.enter_context(_tracing.run_span(
        plan_hash=plan.plan_hash, encoder_kind=plan.encoder.vendor,
    ))
    try:
        summary = _run_full_impl(
            plan, options, emit, cancel_token, pause_token,
        )
    except BaseException as exc:
        log.error(
            "run.failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            wall_clock_sec=round(time.time() - start_ts, 3),
        )
        _maybe_dispatch_notification(
            options, plan, "failed",
            extra_message=f"{type(exc).__name__}: {exc}",
            emit=emit,
        )
        _maybe_record_telemetry(
            options, plan, "failed",
            wall_clock_sec=time.time() - start_ts,
            extra_message=f"{type(exc).__name__}: {exc}",
        )
        # v1.3.0 Task 32 — run-level audit on failure.  Wrapped in its
        # own try/except so an audit-path misconfiguration never masks
        # the original raise.
        try:
            _maybe_record_audit(
                options, plan, "failed",
                run_id=run_id, started_at=started_at_dt,
                segments_total=0, segments_failed=0,
                extra={"error_type": type(exc).__name__,
                       "error_message": str(exc)[:500]},
            )
        except Exception as audit_exc:  # noqa: BLE001 — defensive
            log.warning("audit record on failure failed: %s", audit_exc)
        # v1.3.0 Task 34 — close OTel span on the failure path so the
        # exporter flushes duration_us before the raise propagates.
        _span_stack.close()
        raise
    wall_clock_sec = time.time() - start_ts
    log.info(
        "run.completed",
        segments_done=summary.segments_done,
        wall_clock_sec=round(wall_clock_sec, 3),
        output=str(summary.output),
    )
    _maybe_dispatch_notification(
        options, plan, "completed",
        summary=summary, emit=emit,
    )
    _maybe_record_telemetry(
        options, plan, "completed",
        wall_clock_sec=wall_clock_sec,
        summary=summary,
    )
    # v1.2.0 Task 28 — record this operating point so the next run on
    # the same (resolution, codec, encoder) key can predict ETA and
    # default workers/segment_sec to what actually scaled.  Never
    # raises; cache failures are logged at WARN inside record_run.
    from yt_uniquifier.core import pgo as _pgo
    if plan.source.video:
        v = plan.source.video[0]
        _pgo.record_run(
            source_width=v.width, source_height=v.height,
            source_duration_sec=plan.source.duration_sec,
            codec=plan.profile.target_codec,
            encoder_kind=plan.encoder.vendor,
            workers=options.workers,
            segment_sec=options.target_segment_sec,
            wall_clock_sec=wall_clock_sec,
        )
    # v1.3.0 Task 32 — run-level audit on success.
    # v1.3.0 Task 34 — close OTel span on the success path before
    # downstream telemetry so duration_us reflects the run only.
    _span_stack.close()
    try:
        _maybe_record_audit(
            options, plan, "completed",
            run_id=run_id, started_at=started_at_dt,
            segments_total=summary.segments_done,
            segments_failed=0,
        )
    except Exception as audit_exc:  # noqa: BLE001 — defensive
        log.warning("audit record on completion failed: %s", audit_exc)
    return summary


def _maybe_record_audit(
    options: RunOptions,
    plan: Plan,
    result: Literal["completed", "failed"],
    *,
    run_id: str,
    started_at: datetime.datetime,
    segments_total: int,
    segments_failed: int,
    extra: dict[str, object] | None = None,
) -> None:
    """Resolve the effective audit log path + call core.audit.record_run."""
    import datetime as _dt

    from yt_uniquifier.core import audit as _audit
    path = _audit.resolve_audit_log_path(options.audit_log_path)
    if path is None:
        return
    _audit.record_run(
        audit_log_path=path,
        run_id=run_id,
        started_at=started_at,
        ended_at=_dt.datetime.now(tz=_dt.UTC),
        input_path=plan.source.path,
        output_path=options.output,
        plan_hash=plan.plan_hash,
        result=result,
        segments_total=segments_total,
        segments_failed=segments_failed,
        principal=options.audit_principal,
        extra=extra,
    )


def _ensure_run_id(options: RunOptions) -> RunOptions:
    """Return ``options`` with a populated ``run_id`` field.

    No-op if the caller already supplied one. v1.1.0 Task 14: keeps the
    web layer's flow simple — it pre-generates the ID so the HTTP
    response can echo it back, and the orchestrator picks up the same
    ID without overwriting. Standalone CLI / GUI runs use this fallback.
    """
    if options.run_id:
        return options
    return dataclasses.replace(options, run_id=_new_run_id())


def _new_run_id() -> str:
    """Return a time-sortable correlation ID.

    Prefers ``uuid.uuid7`` (Python 3.13+); falls back to ``uuid4`` on
    older runtimes so behaviour is consistent without forcing every
    deployment onto 3.13. The first 12 chars of a uuid4 still give us
    ~46 bits of entropy which is plenty to rule out collisions inside
    one process.
    """
    import uuid
    uuid7 = getattr(uuid, "uuid7", None)
    if callable(uuid7):
        return str(uuid7())
    return str(uuid.uuid4())


def _run_full_impl(
    plan: Plan,
    options: RunOptions,
    emit: Callable[[RunEvent], None],
    cancel_token: CancelToken | None,
    pause_token: PauseToken | None = None,
) -> RunSummary:
    expected_suffixes = {
        "mp4": {".mp4", ".m4v"},
        "mov": {".mov"},
        "mkv": {".mkv"},
    }[plan.profile.output_container]
    if options.output.suffix.lower() not in expected_suffixes:
        expected = ", ".join(sorted(expected_suffixes))
        raise PipelineError(
            f"output suffix {options.output.suffix!r} conflicts with profile "
            f"container {plan.profile.output_container!r}; expected {expected}"
        )

    findings = preflight(
        plan.source, plan, plan.encoder, work_dir=options.work_dir,
        accept_watermark_risk=options.accept_watermark_risk,
        verify_encoder_capability=True,
    )
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
    # The thread is daemon=True so process shutdown doesn't block on it,
    # but we still drop the stop event in a try/finally so per-test
    # observer threads don't accumulate across the long pytest run.
    # Without this, an exception (preflight fail, cancel, segment error)
    # would leak one daemon thread per call — CI matrices that run the
    # full suite then OOM / time out on thread limits.
    _pause_stop_event = _start_pause_observer(
        pause_token, cancel_token, store, emit,
    )
    try:
        return _run_full_body(
            plan, options, emit, cancel_token, pause_token,
            store, segments, findings,
        )
    finally:
        _pause_stop_event.set()
        store.close()


def _run_full_body(
    plan: Plan,
    options: RunOptions,
    emit: Callable[[RunEvent], None],
    cancel_token: CancelToken | None,
    pause_token: PauseToken | None,
    store: CheckpointStore,
    segments: list[Segment],
    findings: list[PreflightFinding],
) -> RunSummary:
    """The hot-loop half of ``_run_full_impl`` — extracted so the pause
    observer cleanup in the caller's ``finally`` always fires, even on
    cancel / segment failure / preflight late-raise.
    """

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
        if store.output_is_valid(options.output):
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
            # v1.0.1: stream a SHA-256 of the encoded segment so the
            # next resume can detect a truncated or corrupted segment
            # file and demote it back to ``pending``. On a modern CPU
            # this runs at ~500 MB/s; an OS read error here is rare but
            # non-fatal — record the segment as done without a hash and
            # let the next resume cycle accept it (legacy-state-file
            # path in checkpoint._verify_done_segments_on_disk).
            try:
                from yt_uniquifier.core.checkpoint import sha256_file
                seg_sha256: str | None = sha256_file(out)
            except OSError:
                seg_sha256 = None
            store.mark(idx, "done", src_path=src, out_path=out, sha256=seg_sha256)
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

    # v1.0.1: force a flush after the segment loop so the per-segment
    # sha256s recorded by ``mark(..., sha256=...)`` land in state.json
    # before we move on. Without this the marks stay in the debounce
    # window and a process exit (or a follow-up resume that touches a
    # branch which doesn't call set_loudnorm / set_main_audio) would
    # read a stale "pending" for segments that finished successfully.
    store.flush()

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
    selected_audio_indices = selected_audio_relative_indices(
        plan.source, plan.profile.audio_tracks,
    )
    concat_segments(
        final_segments,
        main_audio,
        options.output,
        build_metadata_args(plan, title_template=options.title_template),
        work_dir=options.work_dir,
        media_source=plan.source.path,
        map_chapters_from=plan.source.path if plan.source.chapters else None,
        audio_source_indices=selected_audio_indices,
        audio_streams=[plan.source.audio[index] for index in selected_audio_indices],
        subtitle_codecs=[stream.codec for stream in plan.source.subtitle],
        auxiliary_streams=list(get_auxiliary_streams(plan.source)),
        target_duration_sec=expected_output_duration(plan),
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

    require_output_contract(plan, options.output)
    store.set_output(options.output)

    emit(RunEvent(kind="done", payload={"output": str(options.output)}))

    if not options.keep_segments:
        _cleanup_artifacts(store, main_audio, emit)

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


def _maybe_record_telemetry(
    options: RunOptions,
    plan: Plan,
    event_kind: Literal["completed", "failed"],
    *,
    wall_clock_sec: float,
    summary: RunSummary | None = None,
    extra_message: str | None = None,
) -> None:
    """Record one run-summary telemetry event when telemetry is enabled.

    Deliberately narrow payload: profile id, encoder fingerprint,
    segment count, wall-clock, OS / Python version. No paths, no
    durations, no audio fingerprints. Path-redaction is still applied
    inside ``telemetry.record`` for the few string fields that *could*
    contain ``$HOME`` (output basename only, never the full path).
    """
    if options.telemetry is None or not options.telemetry.enabled:
        return
    try:
        import platform
        import sys
        event: dict[str, object] = {
            "kind": "run_summary",
            "status": event_kind,
            "profile_name": plan.profile.name,
            "profile_codec": plan.profile.target_codec,
            "encoder_name": plan.encoder.name,
            "encoder_vendor": plan.encoder.vendor,
            "wall_clock_sec": round(wall_clock_sec, 2),
            "workers": options.workers,
            "os": sys.platform,
            "os_release": platform.release(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }
        if summary is not None:
            event["segments_done"] = summary.segments_done
            event["output_basename"] = summary.output.name
        if extra_message is not None:
            event["error_summary"] = extra_message[:200]
        record_telemetry(event, options.telemetry)
    except Exception as exc:  # noqa: BLE001 — never propagate
        # Telemetry MUST NOT alter run outcomes. Log to the structured
        # logger so a missing event is debuggable, but stay silent on
        # the public RunEvent stream (notifications already do similar).
        import logging
        logging.getLogger(__name__).warning(
            "telemetry record failed: %s", exc,
        )


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
            # Wake immediately on pause/resume.  The one-second timeout is
            # retained for the 24-hour auto-cancel check and as a bounded
            # delay when the caller asks this observer to stop.
            pause_token.wait_for_state_change(now_paused, timeout=1.0)

    threading.Thread(
        target=_observe, daemon=True, name="pause-observer",
    ).start()
    return stop
