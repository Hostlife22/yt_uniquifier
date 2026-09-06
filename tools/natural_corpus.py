"""Validate and run an owned/licensed natural-content benchmark manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from yt_uniquifier.core.models import QAReport
from yt_uniquifier.core.probe import probe
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import verdict
from yt_uniquifier.core.runner import _terminate
from yt_uniquifier.core.transforms.audio_loudnorm import measure

REPO = Path(__file__).resolve().parents[1]
ALLOWED_MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".mxf", ".webm", ".ogv"}
ALLOWED_RIGHTS = {"licensed", "owned", "public_domain"}
ALLOWED_CLASSES = {"sdr", "hdr10", "hlg"}


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    source: Path
    source_rel: str
    rights_status: str
    rights_reference: str
    media_class: str
    review_cues: tuple[str, ...]
    variant_ids: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkVariant:
    variant_id: str
    profile: Path
    encoder: str


@dataclass(frozen=True)
class CorpusManifest:
    path: Path
    profiles: tuple[Path, ...]
    encoders: tuple[str, ...]
    workers: int
    cases: tuple[CorpusCase, ...]
    variants: tuple[BenchmarkVariant, ...]


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _safe_relative(root: Path, value: object, field: str) -> tuple[Path, str]:
    relative = Path(_nonempty_string(value, field))
    if relative.is_absolute():
        raise ValueError(f"{field} must be relative to the manifest directory")
    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{field} escapes the corpus directory")
    return resolved, relative.as_posix()


def _resolve_profile(manifest_path: Path, value: object, field: str) -> Path:
    profile_value = Path(_nonempty_string(value, field))
    profile = (
        profile_value.resolve()
        if profile_value.is_absolute()
        else (manifest_path.parent / profile_value).resolve()
    )
    if not profile.is_file():
        raise ValueError(f"profile does not exist: {value}")
    load_profile(profile)
    return profile


def _safe_id(value: object, field: str) -> str:
    identifier = _nonempty_string(value, field)
    if not all(character.isalnum() or character in "-_" for character in identifier):
        raise ValueError(f"{field} contains unsafe characters: {identifier}")
    return identifier


def load_manifest(path: Path, *, require_media: bool = True) -> CorpusManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise ValueError("matrix must be a mapping")
    workers = matrix.get("workers", 1)
    if not isinstance(workers, int) or not 1 <= workers <= 64:
        raise ValueError("matrix.workers must be an integer in [1, 64]")

    variants: list[BenchmarkVariant] = []
    profiles: list[Path] = []
    encoders: list[str] = []
    variant_values = matrix.get("variants")
    if variant_values is not None:
        if not isinstance(variant_values, list) or not variant_values:
            raise ValueError("matrix.variants must be a non-empty list")
        variant_ids: set[str] = set()
        for index, value in enumerate(variant_values):
            if not isinstance(value, dict):
                raise ValueError(f"matrix.variants[{index}] must be a mapping")
            variant_id = _safe_id(value.get("id"), f"matrix.variants[{index}].id")
            if variant_id in variant_ids:
                raise ValueError(f"duplicate variant id: {variant_id}")
            variant_ids.add(variant_id)
            profile = _resolve_profile(
                path, value.get("profile"), f"matrix.variants[{index}].profile",
            )
            encoder = _nonempty_string(
                value.get("encoder"), f"matrix.variants[{index}].encoder",
            )
            variants.append(BenchmarkVariant(variant_id, profile, encoder))
            profiles.append(profile)
            encoders.append(encoder)
    else:
        profile_values = matrix.get("profiles")
        encoder_values = matrix.get("encoders")
        if not isinstance(profile_values, list) or not profile_values:
            raise ValueError("matrix.profiles must be a non-empty list")
        if not isinstance(encoder_values, list) or not encoder_values:
            raise ValueError("matrix.encoders must be a non-empty list")
        profiles = [
            _resolve_profile(path, value, f"matrix.profiles[{index}]")
            for index, value in enumerate(profile_values)
        ]
        encoders = [
            _nonempty_string(value, f"matrix.encoders[{index}]")
            for index, value in enumerate(encoder_values)
        ]
        variants = [
            BenchmarkVariant(f"{profile.stem}-{encoder}", profile, encoder)
            for profile in profiles
            for encoder in encoders
        ]

    case_values = raw.get("cases")
    if not isinstance(case_values, list) or not case_values:
        raise ValueError("cases must be a non-empty list")
    cases: list[CorpusCase] = []
    ids: set[str] = set()
    for index, value in enumerate(case_values):
        if not isinstance(value, dict):
            raise ValueError(f"cases[{index}] must be a mapping")
        case_id = _safe_id(value.get("id"), f"cases[{index}].id")
        if case_id in ids:
            raise ValueError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        source, source_rel = _safe_relative(
            path.parent, value.get("source"), f"cases[{index}].source"
        )
        if source.suffix.lower() not in ALLOWED_MEDIA_SUFFIXES:
            raise ValueError(f"unsupported media suffix for case {case_id}")
        if require_media and not source.is_file():
            raise ValueError(f"source does not exist for case {case_id}: {source_rel}")
        rights_status = _nonempty_string(
            value.get("rights_status"), f"cases[{index}].rights_status"
        )
        if rights_status not in ALLOWED_RIGHTS:
            raise ValueError(f"unsupported rights_status for case {case_id}")
        rights_reference = _nonempty_string(
            value.get("rights_reference"), f"cases[{index}].rights_reference"
        )
        media_class = _nonempty_string(
            value.get("media_class"), f"cases[{index}].media_class"
        )
        if media_class not in ALLOWED_CLASSES:
            raise ValueError(f"unsupported media_class for case {case_id}")
        cue_values = value.get("review_cues", [])
        if not isinstance(cue_values, list) or not all(
            isinstance(cue, str) and cue.strip() for cue in cue_values
        ):
            raise ValueError(f"review_cues must be a string list for case {case_id}")
        selected_variants = value.get("variants")
        if selected_variants is None:
            case_variant_ids = tuple(variant.variant_id for variant in variants)
        else:
            if not isinstance(selected_variants, list) or not selected_variants:
                raise ValueError(f"variants must be a non-empty list for case {case_id}")
            case_variant_ids = tuple(
                _safe_id(item, f"cases[{index}].variants")
                for item in selected_variants
            )
            if len(set(case_variant_ids)) != len(case_variant_ids):
                raise ValueError(f"duplicate variants for case {case_id}")
            unknown_variants = set(case_variant_ids) - {
                variant.variant_id for variant in variants
            }
            if unknown_variants:
                raise ValueError(
                    f"unknown variants for case {case_id}: "
                    f"{', '.join(sorted(unknown_variants))}"
                )
        cases.append(
            CorpusCase(
                case_id=case_id,
                source=source,
                source_rel=source_rel,
                rights_status=rights_status,
                rights_reference=rights_reference,
                media_class=media_class,
                review_cues=tuple(cue.strip() for cue in cue_values),
                variant_ids=case_variant_ids,
            )
        )
    return CorpusManifest(
        path=path,
        profiles=tuple(profiles),
        encoders=tuple(encoders),
        workers=workers,
        cases=tuple(cases),
        variants=tuple(variants),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _capture(command: list[str], log: Path) -> int:
    try:
        from tools.benchmark import _ProcessTreeMemorySampler
    except ModuleNotFoundError:
        from benchmark import _ProcessTreeMemorySampler  # type: ignore[no-redef]
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.Popen(
            command,
            cwd=REPO,
            text=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        sampler = _ProcessTreeMemorySampler(pid=result.pid)
        sampler.start()
        try:
            returncode = result.wait()
        finally:
            peak_kb, method = sampler.stop()
            if result.poll() is None:
                _terminate(result)
                result.wait()
    log.with_suffix(".resources.json").write_text(json.dumps({
        "wall_sec": time.monotonic() - started,
        "rss_peak_kb": peak_kb if peak_kb > 0 else None, "rss_method": method,
    }, indent=2) + "\n", encoding="utf-8")
    return returncode


_PSNR_RE = re.compile(r"average:([0-9.]+|inf)")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _psnr(source: Path, output: Path) -> tuple[float | None, str | None]:
    """Measure decoded-video PSNR in the same resized domain as QA SSIM."""
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(output),
        "-i", str(source), "-lavfi",
        "[1:v][0:v]scale2ref=w=iw:h=ih[ref][dist];"
        "[dist]setsar=1[d];[ref]setsar=1[r];[d][r]psnr",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, cwd=REPO, capture_output=True, text=True,
            check=False, timeout=3600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"PSNR unavailable: {exc}"
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1] if result.stderr else "unknown"
        return None, f"PSNR failed: {tail}"
    matches = _PSNR_RE.findall(result.stderr)
    if not matches:
        return None, "PSNR unavailable: score not found"
    if matches[-1] == "inf":
        return None, "PSNR is infinite (decoded frames are identical)"
    return float(matches[-1]), None


def _audio_metrics(path: Path) -> tuple[float | None, float | None, str | None]:
    try:
        loudness = measure(path)
    except Exception as exc:  # noqa: BLE001 - metric availability belongs in report
        return None, None, f"audio loudness unavailable: {exc}"
    if not math.isfinite(loudness.input_i) or not math.isfinite(loudness.input_tp):
        return None, None, "audio loudness unavailable: silence/nonfinite measurement"
    return loudness.input_i, loudness.input_tp, None


def _number(payload: dict[str, Any], key: str) -> float | int | None:
    value = payload.get(key)
    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def _required_metric_names(
    *,
    media_class: str,
    keep_hdr: bool,
    source_has_audio: bool,
) -> tuple[str, ...]:
    """Return only metrics that are meaningful in the cell's scoring domain."""
    required = [
        "registered_ssim",
        "psnr_db",
        "output_size_bytes",
        "duration_sec",
        "wall_sec",
        "rss_peak_kb",
    ]
    # Standard VMAF is an SDR model. The registered implementation deliberately
    # declines preserved PQ/HLG until a corpus-qualified HDR domain exists.
    if media_class == "sdr" or not keep_hdr:
        required.append("registered_vmaf")
    if source_has_audio:
        required.extend(("lufs_i", "true_peak_dbtp"))
    return tuple(required)


_CSV_FIELDS = (
    "case_id", "variant_id", "profile", "encoder", "status",
    "vmaf", "registered_vmaf", "ssim", "registered_ssim", "registered_sscd",
    "psnr_db", "lufs_i", "true_peak_dbtp",
    "source_size_bytes", "output_size_bytes", "size_ratio",
    "duration_sec", "wall_sec", "rss_peak_kb",
)


def _source_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": source["case_id"],
        "variant_id": "source",
        "profile": "",
        "encoder": "copy",
        "status": "baseline",
        "vmaf": 100.0,
        "registered_vmaf": 100.0,
        "ssim": 1.0,
        "registered_ssim": 1.0,
        "registered_sscd": None,
        "psnr_db": None,
        "lufs_i": source.get("lufs_i"),
        "true_peak_dbtp": source.get("true_peak_dbtp"),
        "source_size_bytes": source["size_bytes"],
        "output_size_bytes": source["size_bytes"],
        "size_ratio": 1.0,
        "duration_sec": source.get("duration_sec"),
        "wall_sec": None,
        "rss_peak_kb": None,
    }


def _write_summary_csv(
    sources: list[dict[str, Any]],
    cells: list[dict[str, Any]],
    destination: Path,
) -> None:
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for source in sources:
            writer.writerow(_source_row(source))
        for cell in cells:
            metrics = cell.get("metrics", {})
            writer.writerow({
                "case_id": cell["case_id"],
                "variant_id": cell["variant_id"],
                "profile": cell["profile"],
                "encoder": cell["encoder"],
                "status": "measured" if cell["ok"] else "incomplete",
                **{field: metrics.get(field) for field in _CSV_FIELDS[5:]},
            })


def _write_summary_html(
    sources: list[dict[str, Any]],
    cells: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    destination: Path,
) -> None:
    headings = _CSV_FIELDS
    rows: list[str] = []
    for source in sources:
        source_values = [_source_row(source)[field] for field in headings]
        rows.append("<tr>" + "".join(
            f"<td>{html.escape('' if value is None else str(value))}</td>"
            for value in source_values
        ) + "</tr>")
    for cell in cells:
        metrics = cell.get("metrics", {})
        values = [
            cell["case_id"], cell["variant_id"], cell["profile"], cell["encoder"],
            "measured" if cell["ok"] else "incomplete",
            *(metrics.get(field) for field in headings[5:]),
        ]
        rows.append("<tr>" + "".join(
            f"<td>{html.escape('' if value is None else str(value))}</td>"
            for value in values
        ) + "</tr>")
    comparison_json = html.escape(json.dumps(comparisons, indent=2, sort_keys=True))
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Production benchmark</title>"
        "<style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}"
        "th,td{border:1px solid #bbb;padding:.35rem;text-align:right}"
        "th:first-child,td:first-child{text-align:left}pre{background:#eee;padding:1rem}"
        "</style></head><body><h1>Production benchmark</h1><table><thead><tr>"
        + "".join(f"<th>{html.escape(field)}</th>" for field in headings)
        + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        + "<p>Measured means pipeline/metric collection completed, not production acceptance. "
        "Review each cell's QA verdict and human listening/visual assessment separately.</p>"
        + f"<h2>Variant deltas</h2><pre>{comparison_json}</pre></body></html>\n"
    )
    destination.write_text(document, encoding="utf-8")


def _comparisons(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        grouped.setdefault(str(cell["case_id"]), []).append(cell)
    result: list[dict[str, Any]] = []
    delta_fields = (
        "vmaf", "registered_vmaf", "ssim", "registered_ssim", "registered_sscd",
        "psnr_db", "lufs_i", "true_peak_dbtp", "output_size_bytes", "wall_sec",
        "rss_peak_kb",
    )
    for case_id, case_cells in grouped.items():
        baseline = next(
            (cell for cell in case_cells if cell["variant_id"] == "current"),
            case_cells[0],
        )
        baseline_metrics = baseline.get("metrics", {})
        for candidate in case_cells:
            if candidate is baseline:
                continue
            candidate_metrics = candidate.get("metrics", {})
            deltas: dict[str, float] = {}
            for field in delta_fields:
                old = baseline_metrics.get(field)
                new = candidate_metrics.get(field)
                if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                    deltas[field] = round(float(new) - float(old), 6)
            result.append({
                "case_id": case_id,
                "baseline_variant": baseline["variant_id"],
                "candidate_variant": candidate["variant_id"],
                "deltas": deltas,
            })
    return result


def run_manifest(
    manifest: CorpusManifest, results: Path, *, with_sscd: bool,
    decode_timelines: bool = False,
) -> int:
    # A reused work directory can hit run_full's resume fast path and replace a
    # genuine encode baseline with near-zero wall time. Claim a fresh destination
    # atomically; never overwrite retained measurements or another active run.
    try:
        results.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(
            "benchmark results already exist; choose a fresh results directory"
        ) from exc
    cells: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    failed = False
    for case in manifest.cases:
        source_has_audio = True
        try:
            source_meta = probe(case.source)
            source_duration: float | None = source_meta.duration_sec
            source_has_audio = bool(source_meta.audio)
        except Exception:  # noqa: BLE001 - benchmark failure remains cell-local evidence
            source_duration = None
        if source_has_audio:
            source_lufs, source_true_peak, source_audio_note = _audio_metrics(case.source)
        else:
            source_lufs, source_true_peak = None, None
            source_audio_note = "not applicable: source has no audio stream"
        sources.append({
            "case_id": case.case_id,
            "source": case.source_rel,
            "sha256": _sha256(case.source),
            "size_bytes": case.source.stat().st_size,
            "duration_sec": source_duration,
            "lufs_i": source_lufs,
            "true_peak_dbtp": source_true_peak,
            "audio_note": source_audio_note,
        })
        if decode_timelines:
            sources[-1]["decoded_timeline"] = _timeline_metrics(case.source)
        (results / f"source-{case.case_id}.json").write_text(
            json.dumps(sources[-1], indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        for variant in (
            item for item in manifest.variants if item.variant_id in case.variant_ids
        ):
            profile_path = variant.profile
            profile = load_profile(profile_path)
            encoder = variant.encoder
            cell_id = f"{case.case_id}__{variant.variant_id}"
            cell_dir = results / cell_id
            cell_dir.mkdir(parents=True, exist_ok=True)
            output = cell_dir / f"output.{profile.output_container}"
            benchmark_json = cell_dir / "benchmark.json"
            plan_json = cell_dir / "plan.json"
            qa_json = cell_dir / "qa.json"
            benchmark_command = [
                sys.executable,
                str(REPO / "tools" / "benchmark.py"),
                str(case.source),
                "--profile",
                str(profile_path),
                "--out",
                str(output),
                "--encoder",
                encoder,
                "--workers",
                str(manifest.workers),
                "--work-dir",
                str(cell_dir / "work"),
                "--csv",
                str(results / "benchmark-ledger.csv"),
                "--json",
                str(benchmark_json),
                "--plan-json",
                str(plan_json),
                "--accept-watermark-risk",
                "--sample-disk",
            ]
            benchmark_rc = _capture(benchmark_command, cell_dir / "benchmark.log")
            qa_rc: int | None = None
            if benchmark_rc == 0:
                qa_command = [
                    str(REPO / ".venv" / "bin" / "yt-uniq"),
                    "qa",
                    str(case.source),
                    str(output),
                    "--json",
                    str(qa_json),
                    "--html",
                    str(cell_dir / "qa.html"),
                    "--plan-json",
                    str(plan_json),
                ]
                if with_sscd:
                    qa_command.append("--sscd")
                qa_rc = _capture(qa_command, cell_dir / "qa.log")
            pipeline_ok = benchmark_rc == 0 and qa_rc == 0
            benchmark = _load_json(benchmark_json)
            qa = _load_json(qa_json)
            qa_resources = _load_json(cell_dir / "qa.resources.json")
            psnr_db: float | None = None
            psnr_note: str | None = "PSNR skipped because output is unavailable"
            output_lufs: float | None = None
            output_true_peak: float | None = None
            output_audio_note: str | None = "audio metrics skipped because output is unavailable"
            if output.is_file():
                psnr_db, psnr_note = _psnr(case.source, output)
                if source_has_audio:
                    output_lufs, output_true_peak, output_audio_note = _audio_metrics(
                        output
                    )
                else:
                    output_audio_note = "not applicable: source has no audio stream"
            output_size = output.stat().st_size if output.is_file() else None
            source_size = case.source.stat().st_size
            metrics = {
                "vmaf": _number(qa, "vmaf_mean"),
                "ssim": _number(qa, "ssim_mean"),
                "registered_vmaf": _number(qa, "vmaf_registered_mean"),
                "registered_ssim": _number(qa, "ssim_registered_mean"),
                "registered_sscd": _number(qa, "sscd_registered_mean"),
                "psnr_db": psnr_db,
                "lufs_i": output_lufs,
                "true_peak_dbtp": output_true_peak,
                "source_size_bytes": source_size,
                "output_size_bytes": output_size,
                "size_ratio": (
                    round(output_size / max(source_size, 1), 6)
                    if output_size is not None else None
                ),
                "duration_sec": _number(qa, "output_duration_sec"),
                "wall_sec": _number(benchmark, "wall_sec"),
                "rss_peak_kb": _number(benchmark, "rss_peak_kb"),
                "qa_wall_sec": _number(qa_resources, "wall_sec"),
                "qa_rss_peak_kb": _number(qa_resources, "rss_peak_kb"),
                "disk_peak_logical_bytes": _number(benchmark, "disk_peak_logical_bytes"),
                "notes": [
                    note for note in (psnr_note, output_audio_note) if note is not None
                ],
            }
            if decode_timelines and output.is_file():
                metrics["decoded_timeline"] = _timeline_metrics(output)
                if "error" in metrics["decoded_timeline"]:
                    pipeline_ok = False
                if "error" in sources[-1]["decoded_timeline"]:
                    pipeline_ok = False
            required_metrics = _required_metric_names(
                media_class=case.media_class,
                keep_hdr=profile.keep_hdr,
                source_has_audio=source_has_audio,
            )
            missing_metrics = [
                field for field in required_metrics if metrics[field] is None
            ]
            metrics["complete"] = not missing_metrics
            metrics["missing"] = missing_metrics
            cell_ok = pipeline_ok and not missing_metrics
            failed |= not cell_ok
            cells.append({
                "cell_id": cell_id,
                "case_id": case.case_id,
                "variant_id": variant.variant_id,
                "source": case.source_rel,
                "source_sha256": _sha256(case.source),
                "rights_status": case.rights_status,
                "rights_reference": case.rights_reference,
                "media_class": case.media_class,
                "profile": profile_path.name,
                "encoder": encoder,
                "benchmark_exit_code": benchmark_rc,
                "qa_exit_code": qa_rc,
                "ok": cell_ok,
                "qa_verdict": _qa_verdict(qa),
                "production_acceptance": "NOT VERIFIED: human/corpus policy review required",
                "metrics": metrics,
                "review_cues": case.review_cues,
            })
    comparisons = _comparisons(cells)
    summary = {
        "schema_version": 1,
        "manifest": manifest.path.name,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "sources": sources,
        "cells": cells,
        "comparisons": comparisons,
    }
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    _write_summary_csv(sources, cells, results / "summary.csv")
    _write_summary_html(sources, cells, comparisons, results / "summary.html")
    return 1 if failed else 0


def _qa_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = verdict(QAReport.model_validate(payload))
    except ValidationError:
        return {"status": "NOT VERIFIED", "reason": "missing or invalid QA report"}
    return {
        "status": result.band, "correctness": result.correctness,
        "quality": result.quality, "reasons": result.reasons,
    }


def _timeline_metrics(path: Path) -> dict[str, Any]:
    # tools can be executed both as modules and directly from the repository.
    try:
        from tools.media_diagnostics import decoded_timeline
    except ModuleNotFoundError:
        from media_diagnostics import decoded_timeline  # type: ignore[no-redef]
    try:
        return decoded_timeline(path)
    except (OSError, ValueError, TimeoutError) as exc:
        return {"error": str(exc), "status": "NOT VERIFIED"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--allow-missing-media", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("manifest", type=Path)
    run.add_argument("--results", type=Path, required=True)
    run.add_argument("--with-sscd", action="store_true")
    run.add_argument("--decode-timelines", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        manifest = load_manifest(
            args.manifest.resolve(),
            require_media=not getattr(args, "allow_missing_media", False),
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"manifest invalid: {exc}", file=sys.stderr)
        return 2
    selected_cells = sum(len(case.variant_ids) for case in manifest.cases)
    print(
        f"manifest valid: {len(manifest.cases)} case(s), "
        f"{selected_cells} selected matrix cell(s)"
    )
    if args.command == "validate":
        return 0
    try:
        return run_manifest(
            manifest, args.results.resolve(), with_sscd=args.with_sscd,
            decode_timelines=args.decode_timelines,
        )
    except (OSError, ValueError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
