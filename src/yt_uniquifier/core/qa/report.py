"""Aggregate similarity metrics for one (input, output) pair into a QAReport."""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from yt_uniquifier.core.runner import CancelToken, RunEvent

from jinja2 import Environment, FileSystemLoader, select_autoescape

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.media_validation import (
    DecodeEvidence,
    inspect_output_contract,
    inspect_output_decode,
)
from yt_uniquifier.core.models import (
    Plan,
    QACorrectness,
    QALoudness,
    QAQualityPolicy,
    QARegistration,
    QARegistrationDetail,
    QAReport,
    SourceMeta,
)
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.qa import audio_fp, cid_predict, hashes, phash, ssim, vmaf
from yt_uniquifier.core.qa.corpus import Corpus
from yt_uniquifier.core.transforms.hdr_wrap import is_tonemap_active

ProgressFn = Callable[[str, float], None]  # phase name, fraction in [0..1]

Verdict = Literal["invalid", "green", "yellow", "red"]
CorrectnessBand = Literal["valid", "invalid", "not_verified"]
QualityBand = Literal["pass", "warning", "fail", "unavailable"]
SimilarityBand = Literal["low", "moderate", "high", "unavailable"]


@dataclass(frozen=True)
class VerdictResult:
    band: Verdict
    correctness: CorrectnessBand
    quality: QualityBand
    visual_similarity: SimilarityBand
    reasons: list[str]
    correctness_reasons: list[str]
    quality_reasons: list[str]
    similarity_reasons: list[str]


def verdict(report: QAReport) -> VerdictResult:
    """Assess correctness, perceptual quality and similarity independently.

    Similarity is diagnostic: high or low pHash similarity cannot prove rights-system
    behaviour and must not turn an otherwise correct, high-quality output red.  The
    overall band is therefore driven only by correctness and quality evidence.
    """
    correctness_reasons = [
        note for note in report.notes if note.startswith("correctness:")
    ]
    if not report.duration_match and (
        report.correctness is None or report.correctness.scope == "pair_contract"
    ):
        correctness_reasons.append(
            f"correctness: duration mismatch: input {report.input_duration_sec:.3f}s vs "
            f"output {report.output_duration_sec:.3f}s — encode changed length."
        )
    correctness: CorrectnessBand = (
        "invalid" if correctness_reasons else "valid"
    )
    if report.correctness is not None:
        if report.correctness.status == "failed":
            correctness = "invalid"
            correctness_reasons.extend(report.correctness.failure_codes)
        elif report.correctness.status == "not_verified" and correctness != "invalid":
            correctness = "not_verified"
            correctness_reasons.append(report.correctness.note or "Correctness not fully verified.")
    elif report.quality_policy is not None and correctness != "invalid":
        correctness = "not_verified"
        correctness_reasons.append(
            "Explicit gates require correctness evidence absent in this report."
        )

    quality_reasons: list[str] = []
    quality: QualityBand = "unavailable"
    if report.vmaf_mean is not None:
        if not math.isfinite(report.vmaf_mean) or not 0.0 <= report.vmaf_mean <= 100.0:
            quality = "fail"
            quality_reasons.append(f"invalid VMAF result: {report.vmaf_mean!r}.")
        elif report.vmaf_mean < 75:
            quality = "fail"
            quality_reasons.append(f"VMAF {report.vmaf_mean:.1f} < 75 — strong quality drop.")
        elif report.vmaf_mean < 85:
            quality = "warning"
            quality_reasons.append(f"VMAF {report.vmaf_mean:.1f} < 85 — visible quality drop.")
        else:
            quality = "pass"
            quality_reasons.append(f"VMAF {report.vmaf_mean:.1f} is within the quality band.")
    if report.ssim_mean is not None:
        if not math.isfinite(report.ssim_mean) or not 0.0 <= report.ssim_mean <= 1.0:
            quality = "fail"
            quality_reasons.append(f"invalid SSIM result: {report.ssim_mean!r}.")
        elif report.ssim_mean < 0.90 and quality != "fail":
            quality = "warning"
            quality_reasons.append(f"SSIM {report.ssim_mean:.3f} < 0.90 — measurable change.")
        elif quality == "unavailable":
            quality = "pass"
            quality_reasons.append(
                f"SSIM {report.ssim_mean:.3f} is within the quality band."
            )
    if quality == "unavailable":
        quality_reasons.append("No VMAF or SSIM result is available.")

    policy = report.quality_policy
    if policy is not None and (policy.min_vmaf is not None or policy.min_ssim is not None):
        quality, quality_reasons = _policy_quality(report, policy)

    similarity_reasons: list[str] = []
    visual_similarity: SimilarityBand
    if report.phash_samples <= 0:
        visual_similarity = "unavailable"
        similarity_reasons.append("pHash visual similarity is unavailable.")
    elif report.phash_similarity > 0.97:
        visual_similarity = "high"
        similarity_reasons.append(
            f"pHash visual similarity {report.phash_similarity:.3f} > 0.97."
        )
    elif report.phash_similarity > 0.85:
        visual_similarity = "moderate"
        similarity_reasons.append(
            f"pHash visual similarity {report.phash_similarity:.3f} is in 0.85..0.97."
        )
    else:
        visual_similarity = "low"
        similarity_reasons.append(
            f"pHash visual similarity {report.phash_similarity:.3f} <= 0.85."
        )

    band: Verdict
    if correctness == "invalid":
        band = "invalid"
    elif quality == "fail":
        band = "red"
    elif correctness == "not_verified" or quality in {"warning", "unavailable"}:
        band = "yellow"
    else:
        band = "green"
    reasons = [*correctness_reasons, *quality_reasons]
    return VerdictResult(
        band=band,
        correctness=correctness,
        quality=quality,
        visual_similarity=visual_similarity,
        reasons=reasons,
        correctness_reasons=correctness_reasons,
        quality_reasons=quality_reasons,
        similarity_reasons=similarity_reasons,
    )


def _policy_quality(report: QAReport, policy: QAQualityPolicy) -> tuple[QualityBand, list[str]]:
    reasons: list[str] = []
    failed = False
    registered = policy.domain == "registered"
    provenance = report.registration.video if report.registration is not None else None
    for metric, minimum, maximum in (
        ("vmaf", policy.min_vmaf, 100.0), ("ssim", policy.min_ssim, 1.0),
    ):
        if minimum is None:
            continue
        value = getattr(report, f"{metric}{'_registered' if registered else ''}_mean")
        valid_registration = not registered or (
            provenance is not None and provenance.compared_samples > 0
            and provenance.coverage_ratio > 0 and provenance.confidence > 0
        )
        if value is None or not math.isfinite(value) or not valid_registration:
            failed = True
            reasons.append(f"{policy.domain} {metric.upper()}: required measurement unavailable.")
        elif not (-1.0 if metric == "ssim" else 0.0) <= value <= maximum or value < minimum:
            failed = True
            reasons.append(f"{policy.domain} {metric.upper()} {value:g} fails minimum {minimum:g}.")
        else:
            reasons.append(f"{policy.domain} {metric.upper()} {value:g} meets minimum {minimum:g}.")
    return ("fail" if failed else "pass"), reasons


def _correctness_notes(
    src_meta: SourceMeta,
    out_meta: SourceMeta,
    *,
    plan: Plan | None,
    output_path: Path,
    failure_codes: list[str],
) -> list[str]:
    if plan is not None:
        contract = inspect_output_contract(
            plan,
            output_path,
            probed_output=out_meta,
        )
        failure_codes.extend(failure.code for failure in contract.failures)
        return [
            "correctness: "
            f"{failure.code}: expected={failure.expected!r}, actual={failure.actual!r}"
            for failure in contract.failures
        ]

    notes: list[str] = []
    out_video = getattr(out_meta, "video", ())
    src_audio = getattr(src_meta, "audio", ())
    out_audio = getattr(out_meta, "audio", ())
    src_subtitles = getattr(src_meta, "subtitle", ())
    out_subtitles = getattr(out_meta, "subtitle", ())
    src_chapters = getattr(src_meta, "chapters", ())
    out_chapters = getattr(out_meta, "chapters", ())

    if len(out_video) != 1:
        failure_codes.append("pair.video_count")
        notes.append(
            f"correctness: expected one primary video stream, found {len(out_video)}"
        )
    if src_audio and not out_audio:
        failure_codes.append("pair.audio_missing")
        notes.append("correctness: source main audio stream is missing from output")
    if len(out_subtitles) != len(src_subtitles):
        failure_codes.append("pair.subtitle_count")
        notes.append(
            "correctness: subtitle stream count changed "
            f"({len(src_subtitles)} -> {len(out_subtitles)})"
        )
    if len(out_chapters) != len(src_chapters):
        failure_codes.append("pair.chapter_count")
        notes.append(
            "correctness: chapter count changed "
            f"({len(src_chapters)} -> {len(out_chapters)})"
        )
    source_aux = getattr(src_meta, "_auxiliary_streams", ())
    output_aux = getattr(out_meta, "_auxiliary_streams", ())
    for kind in ("attachment", "data", "attached_pic"):
        source_count = sum(stream.kind == kind for stream in source_aux)
        output_count = sum(stream.kind == kind for stream in output_aux)
        if source_count != output_count:
            failure_codes.append(f"pair.{kind}_count")
            notes.append(
                f"correctness: {kind} stream count changed "
                f"({source_count} -> {output_count})"
            )
    return notes


def build_report(
    input_path: Path,
    output_path: Path,
    *,
    plan: Plan | None = None,
    samples: int = 120,
    run_vmaf: bool = True,
    run_ssim: bool = True,
    run_audio_fp: bool = True,
    predict_cid: bool = True,
    vs_corpus: Corpus | None = None,
    progress: ProgressFn | None = None,
    cancel_token: CancelToken | None = None,
    compute_sscd: bool = False,
    sscd_frame_count: int = 32,
    verify_decode: bool = True,
    run_registered: bool = False,
    registration_target_segment_sec: float = 600.0,
    run_loudness: bool = False,
    quality_policy: QAQualityPolicy | None = None,
    decode_evidence: DecodeEvidence | None = None,
) -> QAReport:
    """Collect every metric we can compute for the pair, in order.

    A6 (v0.5.5): ``cancel_token`` is honoured at each phase boundary.
    The long pole is VMAF on hour-long sources; pre-fix Cancel in the
    QA viewer was an outright lie (the QaWorker carried a cancel token
    but no code path read it).
    """
    p = progress or (lambda _n, _f: None)
    notes: list[str] = []
    if quality_policy is not None:
        if quality_policy.min_vmaf is not None and not run_vmaf:
            raise PipelineError("VMAF minimum conflicts with disabled VMAF measurement")
        if quality_policy.min_ssim is not None and not run_ssim:
            raise PipelineError("SSIM minimum conflicts with disabled SSIM measurement")
        if quality_policy.domain == "registered" and (not run_registered or plan is None):
            raise PipelineError("Registered quality policy requires registered metrics and a Plan")

    def _check_cancel(phase: str) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError(f"QA cancelled by user (during {phase})")

    # Probe and validate the media contract before any sampled metric.  A
    # sampled metric can look healthy while an unvisited tail is corrupt or a
    # non-video stream is missing.
    _check_cancel("probe")
    p("probe", 0.0)
    src_meta = probe_file(input_path)
    out_meta = probe_file(output_path)
    try:
        output_identity = DecodeEvidence.capture(output_path)
    except OSError:
        output_identity = None
    if quality_policy is not None and quality_policy.domain == "raw" and (
        quality_policy.min_vmaf is not None
    ) and any(video.color.is_hdr for meta in (src_meta, out_meta) for video in meta.video):
        raise PipelineError(
            "Raw VMAF gating requires SDR/SDR; HDR needs a valid SDR scoring domain"
        )
    p("probe", 1.0)
    failure_codes: list[str] = []
    notes.extend(_correctness_notes(
        src_meta,
        out_meta,
        plan=plan,
        output_path=output_path,
        failure_codes=failure_codes,
    ))
    duration_match = abs(src_meta.duration_sec - out_meta.duration_sec) < 0.5
    if plan is None and not duration_match:
        failure_codes.append("pair.duration")
    correctness = QACorrectness(
        status="failed" if failure_codes else "not_verified",
        scope="plan_contract" if plan is not None else "pair_contract",
        failure_codes=failure_codes,
        note="Full output decode was not performed; internal A/V sync is not established.",
    )

    reused_decode = decode_evidence is not None and decode_evidence.matches(output_path)
    if reused_decode:
        correctness = correctness.model_copy(update={
            "status": "failed" if failure_codes else "passed",
            "full_decode_status": "passed",
            "note": "Reused process-local final decode evidence for unchanged output bytes; "
                    "internal A/V sync requires event checks.",
        })
    if not reused_decode and (verify_decode or decode_evidence is not None) and out_meta.video:
        _check_cancel("decode")
        p("decode", 0.0)
        def _on_decode_event(event: RunEvent) -> None:
            if event.kind != "progress" or out_meta.duration_sec <= 0:
                return
            raw = event.payload.get("out_time_us")
            if not isinstance(raw, str):
                return
            try:
                fraction = int(raw) / 1_000_000 / out_meta.duration_sec
            except ValueError:
                return
            p("decode", max(0.0, min(0.99, fraction)))

        decode_failure = inspect_output_decode(
            output_path,
            on_event=_on_decode_event,
            cancel_token=cancel_token,
        )
        if decode_failure is not None:
            notes.append(f"correctness: full output decode failed: {decode_failure.actual}")
            failure_codes.append(decode_failure.code)
        correctness = correctness.model_copy(update={
            "status": "failed" if failure_codes else "passed",
            "failure_codes": failure_codes,
            "full_decode_status": "failed" if decode_failure is not None else "passed",
            "note": "Contract and decode scope only; internal A/V sync requires event checks.",
        })
        p("decode", 1.0)

    loudness = QALoudness(status="not_verified", note="Full-stream measurement not requested.")
    if run_loudness:
        from yt_uniquifier.core.qa.loudness import measure_output

        _check_cancel("loudness")
        p("loudness", 0.0)
        loudness = measure_output(output_path, out_meta, cancel_token=cancel_token)
        p("loudness", 1.0)

    _check_cancel("md5")
    p("md5", 0.0)
    md5_in = hashes.md5_file(input_path)
    md5_out = hashes.md5_file(output_path)
    p("md5", 1.0)

    _check_cancel("phash")
    p("phash", 0.0)
    try:
        ph = phash.compare(input_path, output_path, n=samples)
    except PipelineError as exc:
        notes.append(f"phash: {exc}")
        ph_samples = 0
        ph_distance_min = 0
        ph_distance_mean = 0.0
        ph_distance_max = 0
        ph_similarity = 0.0
    else:
        ph_samples = ph.samples
        ph_distance_min = ph.distance_min
        ph_distance_mean = ph.distance_mean
        ph_distance_max = ph.distance_max
        ph_similarity = ph.similarity
    p("phash", 1.0)

    af_sim: float | None = None
    af_hamming: float | None = None
    af_confidence: float | None = None
    af_per_window: list[float] | None = None
    af_variance: float | None = None
    registered_audio: audio_fp.RegisteredAudioFP | None = None
    if run_audio_fp:
        _check_cancel("audio_fp")
        p("audio_fp", 0.0)
        fingerprint_analysis = audio_fp.analyze_pair(
            input_path,
            output_path,
            input_duration_sec=src_meta.duration_sec,
            output_duration_sec=out_meta.duration_sec,
        )
        af = fingerprint_analysis.similarity
        if af.available:
            af_sim = af.similarity
        elif af.note:
            notes.append(f"audio_fp: {af.note}")
        afh = fingerprint_analysis.hamming
        if afh.available:
            af_hamming = afh.hamming_per_frame
            af_confidence = afh.match_confidence
        # v0.4.2 — per-window variance KPI.
        afv = fingerprint_analysis.variance
        if afv.available:
            af_per_window = afv.hamming_per_window
            af_variance = afv.variance_between_windows
        if fingerprint_analysis.coverage_note:
            notes.append(f"audio_fp: {fingerprint_analysis.coverage_note}")
        registered_audio = getattr(fingerprint_analysis, "registered", None)
        # If afh/afv.available is False, af.compare() above already produced
        # an equivalent note about fpcalc missing/failing — no second log.
        p("audio_fp", 1.0)

    vmaf_mean: float | None = None
    if run_vmaf:
        _check_cancel("vmaf")
        p("vmaf", 0.0)
        # B5: auto-subsample for long sources. Short clips keep
        # subsample=1 — see vmaf.auto_subsample_for_duration.
        src_fps = src_meta.video[0].fps if src_meta.video else 24.0
        sub = vmaf.auto_subsample_for_duration(
            src_meta.duration_sec, fps=src_fps,
        )
        v = vmaf.compute(input_path, output_path, subsample=sub)
        if v.score is not None:
            vmaf_mean = v.score
        elif v.note:
            notes.append(f"vmaf: {v.note}")
        p("vmaf", 1.0)

    ssim_mean: float | None = None
    if run_ssim:
        _check_cancel("ssim")
        p("ssim", 0.0)
        s = ssim.compute(input_path, output_path)
        if s.score is not None:
            ssim_mean = s.score
        elif s.note:
            notes.append(f"ssim: {s.note}")
        p("ssim", 1.0)

    sscd_mean: float | None = None
    sscd_min: float | None = None
    sscd_per_frame: list[float] | None = None
    if compute_sscd:
        _check_cancel("sscd")
        p("sscd", 0.0)
        # Lazy import — keeps the torch dependency out of the import
        # graph when the metric is unused.
        from yt_uniquifier.core.qa import sscd as _sscd

        try:
            sres = _sscd.compute_sscd(
                input_path, output_path,
                frame_count=sscd_frame_count,
                cancel_token=cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 — opt-in metric; never abort whole report
            # SSCD is opt-in and slow; a missing [ml] extra or a torch
            # runtime fault should land as a NOTE rather than failing
            # the entire QA report (which already collected the cheap
            # metrics). Re-raise only on user cancellation so the
            # cancel button still works.
            if isinstance(exc, PipelineError) and "cancelled" in str(exc):
                raise
            notes.append(f"sscd: {exc}")
        else:
            sscd_mean = sres.mean_similarity
            sscd_min = sres.min_similarity
            sscd_per_frame = list(sres.per_frame)
        p("sscd", 1.0)

    vmaf_registered_mean: float | None = None
    ssim_registered_mean: float | None = None
    sscd_registered_mean: float | None = None
    audio_registered_hamming: float | None = None
    video_registration: QARegistrationDetail | None = None
    audio_registration: QARegistrationDetail | None = None
    if run_registered and plan is None:
        notes.append("registration: exact Plan provenance is required")
    elif run_registered and plan is not None:
        if registered_audio is not None:
            frame_sec = 4096.0 / 11025.0
            offset_sec = (
                registered_audio.offset_frames * frame_sec
                if registered_audio.offset_frames is not None
                else None
            )
            drift_per_hour: float | None = None
            if registered_audio.drift_frames is not None:
                covered_sec = max(
                    min(src_meta.duration_sec, out_meta.duration_sec, 600.0),
                    frame_sec,
                )
                drift_per_hour = max(-3600.0, min(
                    3600.0,
                    registered_audio.drift_frames * frame_sec * 3600.0 / covered_sec,
                ))
            audio_registered_hamming = registered_audio.hamming_per_frame
            audio_registration = QARegistrationDetail(
                offset_sec=offset_sec,
                drift_sec_per_hour=drift_per_hour,
                compared_samples=registered_audio.compared_frames,
                coverage_ratio=registered_audio.coverage_ratio,
                confidence=registered_audio.confidence,
                note=registered_audio.note,
            )

        if plan.source.video and (run_vmaf or run_ssim or compute_sscd):
            _check_cancel("registration")
            p("registration", 0.0)
            from yt_uniquifier.core.qa.registration import build_transformed_reference

            try:
                with tempfile.TemporaryDirectory(prefix="qa_registered_") as tmp:
                    reference = build_transformed_reference(
                        plan,
                        Path(tmp) / "transformed-reference.mkv",
                        target_segment_sec=registration_target_segment_sec,
                        cancel_token=cancel_token,
                        progress=lambda fraction: p("registration", fraction * 0.70),
                    )
                    if reference.path.suffix == ".ffconcat":
                        notes.append("registration: full-coverage virtual FFV1 concat; "
                                     "no second reference copy allocated")
                    preserved_hdr = (
                        plan.source.video[0].color.is_hdr
                        and plan.profile.keep_hdr
                        and not is_tonemap_active(plan.profile.transforms)
                    )
                    if run_vmaf and preserved_hdr:
                        notes.append(
                            "registered_vmaf: unavailable for preserved HDR until "
                            "a corpus-validated HDR scoring domain is configured"
                        )
                    elif run_vmaf:
                        result = vmaf.compute(
                            reference.path,
                            output_path,
                            reset_pts=True,
                            cancel_token=cancel_token,
                        )
                        if result.score is not None:
                            vmaf_registered_mean = result.score
                        elif result.note:
                            notes.append(f"registered_vmaf: {result.note}")
                    p("registration", 0.80)
                    if run_ssim:
                        result_ssim = ssim.compute(
                            reference.path,
                            output_path,
                            reset_pts=True,
                            cancel_token=cancel_token,
                        )
                        if result_ssim.score is not None:
                            ssim_registered_mean = result_ssim.score
                        elif result_ssim.note:
                            notes.append(f"registered_ssim: {result_ssim.note}")
                    p("registration", 0.90)
                    if compute_sscd:
                        from yt_uniquifier.core.qa import sscd as _sscd

                        registered_sscd = _sscd.compute_sscd_registered(
                            reference.path,
                            output_path,
                            frame_count=sscd_frame_count,
                            cancel_token=cancel_token,
                            reference_cache_key=reference.cache_key,
                        )
                        sscd_registered_mean = registered_sscd.mean_similarity
                        video_registration = QARegistrationDetail(
                            offset_sec=registered_sscd.mean_offset_sec,
                            drift_sec_per_hour=None,
                            compared_samples=registered_sscd.compared_frames,
                            coverage_ratio=registered_sscd.coverage_ratio,
                            confidence=registered_sscd.confidence,
                            note=registered_sscd.note,
                        )
                        p("registration", 0.98)
                    else:
                        compared = round(
                            min(src_meta.duration_sec, out_meta.duration_sec)
                            * max(plan.source.video[0].fps, 0.0)
                        )
                        coverage = min(
                            src_meta.duration_sec, out_meta.duration_sec
                        ) / max(src_meta.duration_sec, out_meta.duration_sec, 0.001)
                        video_registration = QARegistrationDetail(
                            offset_sec=0.0,
                            drift_sec_per_hour=0.0,
                            compared_samples=compared,
                            coverage_ratio=coverage,
                            confidence=coverage,
                            note=(
                                f"transformed FFV1 reference replayed across "
                                f"{reference.segments} segment(s); no SSCD alignment requested"
                            ),
                        )
            except Exception as exc:  # noqa: BLE001 - additive metric degrades to note
                if cancel_token is not None and cancel_token.is_cancelled():
                    raise
                unavailable_note = f"registered video metrics unavailable: {exc}"
                notes.append(f"registration: {exc}")
                video_registration = QARegistrationDetail(
                    offset_sec=None,
                    drift_sec_per_hour=None,
                    compared_samples=0,
                    coverage_ratio=0.0,
                    confidence=0.0,
                    note=unavailable_note,
                )
            p("registration", 1.0)

    registration = (
        QARegistration(
            reference_mode="plan_transformed",
            plan_hash=plan.plan_hash,
            run_seed=plan.run_seed,
            video=video_registration,
            audio=audio_registration,
        )
        if run_registered and plan is not None
        and (video_registration is not None or audio_registration is not None)
        else None
    )

    cid_self: float | None = None
    weakest_window: tuple[float, float] | None = None
    chunk_dump: list[dict[str, float]] = []
    corpus_dump: list[dict[str, float | str]] = []
    if predict_cid:
        _check_cancel("cid_predict")
        p("cid_predict", 0.0)
        cid = cid_predict.predict(
            input_path, output_path,
            corpus=vs_corpus,
        )
        cid_self = cid.match_probability_self
        if cid.weakest_chunk is not None:
            weakest_window = (cid.weakest_chunk.start_sec, cid.weakest_chunk.end_sec)
        chunk_dump = [
            {
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "visual": c.visual_similarity,
                "audio": c.audio_similarity,
                "combined": c.combined,
            }
            for c in cid.chunks
        ]
        corpus_dump = [
            {
                "id": m.entry.id,
                "path": str(m.entry.path),
                "visual": m.visual_similarity,
                "audio": m.audio_similarity,
                "combined": m.combined,
            }
            for m in cid.corpus_matches
        ]
        p("cid_predict", 1.0)

    if output_identity is not None and not output_identity.matches(output_path):
        failure_codes.append("output.changed_during_qa")
        correctness = correctness.model_copy(update={
            "status": "failed", "failure_codes": failure_codes,
            "note": "Output changed while QA was running; discard measurements.",
        })

    return QAReport(
        correctness=correctness,
        loudness=loudness,
        quality_policy=quality_policy,
        input_md5=md5_in,
        output_md5=md5_out,
        input_size_bytes=src_meta.size_bytes,
        output_size_bytes=out_meta.size_bytes,
        input_duration_sec=src_meta.duration_sec,
        output_duration_sec=out_meta.duration_sec,
        phash_samples=ph_samples,
        phash_distance_min=ph_distance_min,
        phash_distance_mean=ph_distance_mean,
        phash_distance_max=ph_distance_max,
        phash_similarity=ph_similarity,
        audio_fp_similarity=af_sim,
        audio_fp_hamming_per_frame=af_hamming,
        audio_fp_match_confidence=af_confidence,
        audio_fp_hamming_per_window=af_per_window,
        audio_fp_hamming_variance=af_variance,
        vmaf_mean=vmaf_mean,
        ssim_mean=ssim_mean,
        duration_match=duration_match,
        notes=notes,
        cid_predict_self=cid_self,
        weakest_chunk_sec=weakest_window,
        chunk_similarities=chunk_dump,
        corpus_matches=corpus_dump,
        sscd_mean=sscd_mean,
        sscd_min=sscd_min,
        sscd_per_frame=sscd_per_frame,
        vmaf_registered_mean=vmaf_registered_mean,
        ssim_registered_mean=ssim_registered_mean,
        sscd_registered_mean=sscd_registered_mean,
        audio_fp_registered_hamming_per_frame=audio_registered_hamming,
        registration=registration,
    )


def write_json(report: QAReport, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


_TEMPLATE_DIR = Path(__file__).parent / "templates"


def heatmap_color(value: float) -> str:
    """Map 0..1 similarity to a CSS rgb() string (green→yellow→red).

    0   → bright green  (#2ecc71)
    0.5 → yellow        (#f1c40f)
    1   → red           (#e74c3c)
    Linear interpolation through HSL hue space (green=120°, red=0°).
    """
    v = max(0.0, min(1.0, value))
    # hue 120 (green) at v=0 → hue 0 (red) at v=1
    hue = (1.0 - v) * 120.0
    return f"hsl({hue:.0f}, 70%, 55%)"


def render_html(report: QAReport, plan: Plan | None, dest: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["heatmap_color"] = heatmap_color
    tpl = env.get_template("report.html.j2")
    v = verdict(report)
    html = tpl.render(
        report=report,
        plan=plan,
        assessment=v,
        verdict_band=v.band,
        verdict_reasons=v.reasons,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
