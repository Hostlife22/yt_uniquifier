"""End-to-end orchestration: probe → preflight → segment → resume → concat → metadata.

`run_full` is the single entry point that CLI (cmd_run, cmd_batch) and the
GUI Worker all call. It accepts an event callback so any UI can stream
progress events (RunEvent + custom phase markers).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.checkpoint import CheckpointStore
from yt_uniquifier.core.encoder import detect_encoders, pick_encoder
from yt_uniquifier.core.errors import PipelineError, PreflightFailure
from yt_uniquifier.core.metadata import build_metadata_args
from yt_uniquifier.core.models import Plan, Profile
from yt_uniquifier.core.pipeline import compute_plan_hash
from yt_uniquifier.core.preflight import PreflightFinding, has_fail, preflight
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.runner import CancelToken, RunEvent
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
) -> RunSummary:
    """Process one input from probe to final mp4."""
    emit = on_event or (lambda _e: None)

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
        # Output gone but state.json reports done → segments were cleaned
        # up after a successful concat. Reset segments to pending so the
        # run re-processes from scratch instead of failing in concat
        # with "Impossible to open seg_0000.mkv".
        out_paths_exist = any(
            s.out_path and s.out_path.exists()
            for s in segments
        )
        if not out_paths_exist:
            emit(RunEvent(kind="log", payload={
                "phase": "resume",
                "message": (
                    "state.json reports all segments done but output and "
                    "segment files are missing — resetting to fresh run"
                ),
            }))
            for s in segments:
                store.mark(s.idx, "pending")
            segments = store.all_segments()

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

        def _on_segment_done(idx: int, src: Path, out: Path) -> None:
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

        try:
            process_video_segments_parallel(
                pending, plan, options.work_dir,
                workers=options.workers,
                on_event=lambda e: emit(RunEvent(
                    kind=e.kind,
                    payload={**e.payload, "phase": "segment"},
                )),
                cancel_token=cancel_token,
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
        )
        if measurement is not None:
            store.set_loudnorm(measurement)
        if main_audio is not None:
            store.set_main_audio(main_audio)

    # Concat + metadata.
    final_segments = [s.out_path for s in store.all_segments() if s.out_path]
    if not final_segments:
        raise PipelineError("no processed segments to concatenate")
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

    return RunSummary(
        output=options.output,
        plan=plan,
        segments_done=len(final_segments),
        preflight_findings=findings,
    )


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
