"""Validate and run an owned/licensed natural-content benchmark manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from yt_uniquifier.core.profile_loader import load_profile

REPO = Path(__file__).resolve().parents[1]
ALLOWED_MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".mxf", ".webm"}
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


@dataclass(frozen=True)
class CorpusManifest:
    path: Path
    profiles: tuple[Path, ...]
    encoders: tuple[str, ...]
    workers: int
    cases: tuple[CorpusCase, ...]


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


def load_manifest(path: Path, *, require_media: bool = True) -> CorpusManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise ValueError("matrix must be a mapping")
    profile_values = matrix.get("profiles")
    encoder_values = matrix.get("encoders")
    workers = matrix.get("workers", 1)
    if not isinstance(profile_values, list) or not profile_values:
        raise ValueError("matrix.profiles must be a non-empty list")
    if not isinstance(encoder_values, list) or not encoder_values:
        raise ValueError("matrix.encoders must be a non-empty list")
    if not isinstance(workers, int) or not 1 <= workers <= 64:
        raise ValueError("matrix.workers must be an integer in [1, 64]")

    profiles: list[Path] = []
    for index, value in enumerate(profile_values):
        profile_value = Path(_nonempty_string(value, f"matrix.profiles[{index}]"))
        profile = (
            profile_value.resolve()
            if profile_value.is_absolute()
            else (path.parent / profile_value).resolve()
        )
        if not profile.is_file():
            raise ValueError(f"profile does not exist: {value}")
        load_profile(profile)
        profiles.append(profile)
    encoders = tuple(
        _nonempty_string(value, f"matrix.encoders[{index}]")
        for index, value in enumerate(encoder_values)
    )

    case_values = raw.get("cases")
    if not isinstance(case_values, list) or not case_values:
        raise ValueError("cases must be a non-empty list")
    cases: list[CorpusCase] = []
    ids: set[str] = set()
    for index, value in enumerate(case_values):
        if not isinstance(value, dict):
            raise ValueError(f"cases[{index}] must be a mapping")
        case_id = _nonempty_string(value.get("id"), f"cases[{index}].id")
        if case_id in ids:
            raise ValueError(f"duplicate case id: {case_id}")
        if not all(character.isalnum() or character in "-_" for character in case_id):
            raise ValueError(f"case id contains unsafe characters: {case_id}")
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
        cases.append(
            CorpusCase(
                case_id=case_id,
                source=source,
                source_rel=source_rel,
                rights_status=rights_status,
                rights_reference=rights_reference,
                media_class=media_class,
                review_cues=tuple(cue.strip() for cue in cue_values),
            )
        )
    return CorpusManifest(
        path=path,
        profiles=tuple(profiles),
        encoders=encoders,
        workers=workers,
        cases=tuple(cases),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _capture(command: list[str], log: Path) -> int:
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            command,
            cwd=REPO,
            text=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return result.returncode


def run_manifest(manifest: CorpusManifest, results: Path, *, with_sscd: bool) -> int:
    results.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    failed = False
    for case in manifest.cases:
        for profile_path in manifest.profiles:
            profile = load_profile(profile_path)
            for encoder in manifest.encoders:
                cell_id = f"{case.case_id}__{profile_path.stem}__{encoder}"
                cell_dir = results / cell_id
                cell_dir.mkdir(parents=True, exist_ok=True)
                output = cell_dir / f"output.{profile.output_container}"
                benchmark_json = cell_dir / "benchmark.json"
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
                    str(results / "benchmark.csv"),
                    "--json",
                    str(benchmark_json),
                    "--accept-watermark-risk",
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
                    ]
                    if with_sscd:
                        qa_command.append("--sscd")
                    qa_rc = _capture(qa_command, cell_dir / "qa.log")
                cell_ok = benchmark_rc == 0 and qa_rc == 0
                failed |= not cell_ok
                cells.append(
                    {
                        "cell_id": cell_id,
                        "case_id": case.case_id,
                        "source": case.source_rel,
                        "source_sha256": _sha256(case.source),
                        "rights_status": case.rights_status,
                        "rights_reference": case.rights_reference,
                        "media_class": case.media_class,
                        "profile": profile_path.name,
                        "encoder": encoder,
                        "benchmark_exit_code": benchmark_rc,
                        "qa_exit_code": qa_rc,
                        "review_cues": case.review_cues,
                    }
                )
    summary = {
        "schema_version": 1,
        "manifest": manifest.path.name,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "cells": cells,
    }
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


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
    print(
        f"manifest valid: {len(manifest.cases)} case(s), "
        f"{len(manifest.profiles) * len(manifest.encoders)} cell(s) per case"
    )
    if args.command == "validate":
        return 0
    return run_manifest(manifest, args.results.resolve(), with_sscd=args.with_sscd)


if __name__ == "__main__":
    raise SystemExit(main())
