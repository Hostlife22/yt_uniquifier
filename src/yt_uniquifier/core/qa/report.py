"""Aggregate similarity metrics for one (input, output) pair into a QAReport."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from yt_uniquifier.core.models import Plan, QAReport
from yt_uniquifier.core.probe import probe as probe_file
from yt_uniquifier.core.qa import audio_fp, hashes, phash, ssim, vmaf

ProgressFn = Callable[[str, float], None]  # phase name, fraction in [0..1]

Verdict = Literal["green", "yellow", "red"]


@dataclass(frozen=True)
class VerdictResult:
    band: Verdict
    reasons: list[str]


def verdict(report: QAReport) -> VerdictResult:
    """Cheap rule of thumb for the HTML banner."""
    reasons: list[str] = []
    band: Verdict = "green"

    # pHash similarity: too high = barely unique; too low = unrecognisable.
    if report.phash_similarity > 0.97:
        band = "red"
        reasons.append(
            f"pHash similarity {report.phash_similarity:.3f} > 0.97 — output too close to input."
        )
    elif report.phash_similarity > 0.85:
        if band == "green":
            band = "yellow"
        reasons.append(
            f"pHash similarity {report.phash_similarity:.3f} in 0.85..0.97 band."
        )
    elif report.phash_similarity < 0.50:
        band = "red"
        reasons.append(
            f"pHash similarity {report.phash_similarity:.3f} < 0.50 — too different visually."
        )

    if report.vmaf_mean is not None:
        if report.vmaf_mean < 75:
            band = "red"
            reasons.append(f"VMAF {report.vmaf_mean:.1f} < 75 — strong quality drop.")
        elif report.vmaf_mean < 85 and band == "green":
            band = "yellow"
            reasons.append(f"VMAF {report.vmaf_mean:.1f} < 85 — visible quality drop.")

    if report.ssim_mean is not None and report.ssim_mean < 0.90 and band == "green":
        band = "yellow"
        reasons.append(f"SSIM {report.ssim_mean:.3f} < 0.90 — measurable change.")

    if not reasons:
        reasons.append("All metrics within expected bands.")
    return VerdictResult(band=band, reasons=reasons)


def build_report(
    input_path: Path,
    output_path: Path,
    *,
    samples: int = 120,
    run_vmaf: bool = True,
    run_ssim: bool = True,
    run_audio_fp: bool = True,
    progress: ProgressFn | None = None,
) -> QAReport:
    """Collect every metric we can compute for the pair, in order."""
    p = progress or (lambda _n, _f: None)
    notes: list[str] = []

    p("md5", 0.0)
    md5_in = hashes.md5_file(input_path)
    md5_out = hashes.md5_file(output_path)
    p("md5", 1.0)

    p("phash", 0.0)
    ph = phash.compare(input_path, output_path, n=samples)
    p("phash", 1.0)

    af_sim: float | None = None
    if run_audio_fp:
        p("audio_fp", 0.0)
        af = audio_fp.compare(input_path, output_path)
        if af.available:
            af_sim = af.similarity
        elif af.note:
            notes.append(f"audio_fp: {af.note}")
        p("audio_fp", 1.0)

    vmaf_mean: float | None = None
    if run_vmaf:
        p("vmaf", 0.0)
        v = vmaf.compute(output_path, input_path)
        if v.score is not None:
            vmaf_mean = v.score
        elif v.note:
            notes.append(f"vmaf: {v.note}")
        p("vmaf", 1.0)

    ssim_mean: float | None = None
    if run_ssim:
        p("ssim", 0.0)
        s = ssim.compute(output_path, input_path)
        if s.score is not None:
            ssim_mean = s.score
        elif s.note:
            notes.append(f"ssim: {s.note}")
        p("ssim", 1.0)

    p("probe", 0.0)
    src_meta = probe_file(input_path)
    out_meta = probe_file(output_path)
    p("probe", 1.0)

    duration_match = abs(src_meta.duration_sec - out_meta.duration_sec) < 0.5

    return QAReport(
        input_md5=md5_in,
        output_md5=md5_out,
        input_size_bytes=src_meta.size_bytes,
        output_size_bytes=out_meta.size_bytes,
        input_duration_sec=src_meta.duration_sec,
        output_duration_sec=out_meta.duration_sec,
        phash_samples=ph.samples,
        phash_distance_min=ph.distance_min,
        phash_distance_mean=ph.distance_mean,
        phash_distance_max=ph.distance_max,
        phash_similarity=ph.similarity,
        audio_fp_similarity=af_sim,
        vmaf_mean=vmaf_mean,
        ssim_mean=ssim_mean,
        duration_match=duration_match,
        notes=notes,
    )


def write_json(report: QAReport, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html(report: QAReport, plan: Plan | None, dest: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tpl = env.get_template("report.html.j2")
    v = verdict(report)
    html = tpl.render(
        report=report,
        plan=plan,
        verdict_band=v.band,
        verdict_reasons=v.reasons,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
