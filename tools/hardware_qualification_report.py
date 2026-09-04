"""Collect reproducible runner and output evidence for hardware qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_OUTPUT_LIMIT = 128_000
_MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}


def _tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-_OUTPUT_LIMIT:]


def _run(command: list[str], *, timeout: int = 30) -> dict[str, object]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"command": command, "available": False}
    try:
        proc = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "available": True,
            "timed_out": True,
            "stdout": _tail(exc.stdout),
            "stderr": _tail(exc.stderr),
        }
    return {
        "command": command,
        "available": True,
        "returncode": proc.returncode,
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "stream=index,codec_name,codec_long_name,profile,level,codec_tag_string,"
        "pix_fmt,width,height,r_frame_rate,avg_frame_rate,has_b_frames,color_range,"
        "color_space,color_transfer,color_primaries:"
        "frame=media_type,key_frame,pict_type,best_effort_timestamp_time:"
        "format=format_name,duration,size,bit_rate",
        "-of", "json",
        str(path),
    ]
    result = _run(command, timeout=60)
    stdout = result.get("stdout")
    if result.get("returncode") == 0 and isinstance(stdout, str):
        try:
            command_result = {key: value for key, value in result.items() if key != "stdout"}
            return {"command_result": command_result, "probe": json.loads(stdout)}
        except json.JSONDecodeError:
            pass
    return {"command_result": result}


def _media_results(root: Path, output: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _MEDIA_SUFFIXES:
            continue
        if path.resolve() == output.resolve():
            continue
        results.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            **_probe(path),
        })
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.media_root.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    requested = [
        value.strip()
        for value in os.environ.get("YT_UNIQ_HARDWARE_ENCODERS", "").split(",")
        if value.strip()
    ]
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "requested_encoders": requested,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version,
        },
        "runner": {
            name: os.environ.get(name)
            for name in ("GITHUB_SHA", "RUNNER_ARCH", "RUNNER_NAME", "RUNNER_OS")
            if os.environ.get(name)
        },
        "tools": {
            "ffmpeg": _run(["ffmpeg", "-version"]),
            "ffmpeg_encoders": _run(["ffmpeg", "-hide_banner", "-encoders"]),
            "ffprobe": _run(["ffprobe", "-version"]),
        },
        "accelerators": {
            "nvidia_smi": _run([
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ]),
            "vainfo": _run(["vainfo"]),
            "lspci_vga": _run(["lspci", "-nnk", "-d", "::0300"]),
            "lspci_3d": _run(["lspci", "-nnk", "-d", "::0302"]),
            "system_profiler": _run(["system_profiler", "SPDisplaysDataType"]),
            "windows_video_controller": _run([
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,DriverVersion,VideoProcessor | ConvertTo-Json",
            ]),
        },
        "media": _media_results(root, output),
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
