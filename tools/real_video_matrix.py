"""Real-video bug-hunt harness.

Walks an inputs directory, builds a (input × profile × encoder × workers) matrix,
runs yt-uniq via subprocess, captures exit code + stderr tail + qa.json, writes
summary.csv. Also runs a resume cell: SIGINT after first segment_done, restart,
verify state.json idempotency.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO / "src" / "yt_uniquifier" / "profiles"
YT_UNIQ = REPO / ".venv" / "bin" / "yt-uniq"
STDERR_TAIL_BYTES = 4000


def _profile_path(name: str) -> Path:
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Profile not found: {p}")
    return p


@dataclass
class Cell:
    input_path: str
    input_class: str
    profile: str
    encoder: str
    workers: int
    mode: str
    exit_code: int
    duration_s: float
    cid_predict_self: float | None
    ssim_mean: float | None
    vmaf_mean: float | None
    phash_similarity: float | None
    qa_path: str
    stderr_tail: str
    out_path: str


def _probe_class(p: Path) -> str:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,color_transfer,pix_fmt",
             "-of", "default=nw=1", str(p)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        attrs = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                attrs[k] = v
        w = int(attrs.get("width", "0"))
        h = int(attrs.get("height", "0"))
        xfer = attrs.get("color_transfer", "")
        if xfer in ("smpte2084",):
            return "hdr10"
        if xfer in ("arib-std-b67",):
            return "hlg"
        if w % 2 or h % 2:
            return "odd_dim"
        if w >= 3000:
            return "4k"
        return f"sdr_{h}p"
    except Exception:
        return "unknown"


def _stderr_tail(text: str) -> str:
    if not text:
        return ""
    b = text.encode("utf-8", errors="replace")
    if len(b) <= STDERR_TAIL_BYTES:
        return text
    return "...[truncated]...\n" + b[-STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")


def _qa_metrics(qa_path: Path) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (cid_predict_self, ssim_mean, vmaf_mean, phash_similarity)."""
    if not qa_path.exists():
        return (None, None, None, None)
    try:
        data = json.loads(qa_path.read_text())
        def g(k: str) -> float | None:
            v = data.get(k)
            return float(v) if isinstance(v, (int, float)) else None
        return (g("cid_predict_self"), g("ssim_mean"), g("vmaf_mean"), g("phash_similarity"))
    except Exception:
        return (None, None, None, None)


def _run_cell(
    inp: Path,
    profile: str,
    encoder: str,
    workers: int,
    out_dir: Path,
    timeout_s: int,
) -> Cell:
    cell_dir = out_dir / f"{inp.stem}__{profile}__{encoder}__w{workers}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    work_dir = cell_dir / "work"
    out_file = cell_dir / f"{inp.stem}.out.mp4"

    cmd = [
        str(YT_UNIQ), "run", str(inp),
        "--profile", str(_profile_path(profile)),
        "--out", str(out_file),
        "--encoder", encoder,
        "--workers", str(workers),
        "--work-dir", str(work_dir),
        "--no-progress",
        "--fast-qa",
    ]
    t0 = time.time()
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
            cwd=str(REPO),
        )
        exit_code = res.returncode
        stderr = res.stderr or ""
    except subprocess.TimeoutExpired as e:
        exit_code = 124
        stderr = f"TIMEOUT after {timeout_s}s\n" + (e.stderr or "")
    dt = time.time() - t0

    qa_path = out_file.with_suffix(".mp4.qa.json")
    if not qa_path.exists():
        qa_path = out_file.with_name(out_file.stem + ".qa.json")

    # Write per-cell log
    (cell_dir / "stderr.log").write_text(stderr)

    cid, ssim, vmaf, phash = _qa_metrics(qa_path)
    return Cell(
        input_path=str(inp),
        input_class=_probe_class(inp),
        profile=profile,
        encoder=encoder,
        workers=workers,
        mode="run",
        exit_code=exit_code,
        duration_s=round(dt, 2),
        cid_predict_self=cid,
        ssim_mean=ssim,
        vmaf_mean=vmaf,
        phash_similarity=phash,
        qa_path=str(qa_path) if qa_path.exists() else "",
        stderr_tail=_stderr_tail(stderr),
        out_path=str(out_file) if out_file.exists() else "",
    )


def _run_resume_cell(
    inp: Path,
    profile: str,
    encoder: str,
    out_dir: Path,
    timeout_s: int,
) -> Cell:
    """SIGINT after first segment_done in stderr, restart, verify state.json advances."""
    cell_dir = out_dir / f"{inp.stem}__{profile}__{encoder}__RESUME"
    if cell_dir.exists():
        shutil.rmtree(cell_dir)
    cell_dir.mkdir(parents=True, exist_ok=True)
    work_dir = cell_dir / "work"
    out_file = cell_dir / f"{inp.stem}.out.mp4"

    cmd = [
        str(YT_UNIQ), "run", str(inp),
        "--profile", str(_profile_path(profile)),
        "--out", str(out_file),
        "--encoder", encoder,
        "--workers", "1",
        "--work-dir", str(work_dir),
        "--no-progress",
        "--fast-qa",
        "--segment-sec", "15",  # force multi-segment on 90s clip
    ]
    t0 = time.time()

    # Pass 1: kill after seeing segment progress
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(REPO),
    )
    pass1_log: list[str] = []
    killed = False
    deadline = t0 + min(60, timeout_s)
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
            continue
        pass1_log.append(line)
        if "segment" in line.lower() and ("done" in line.lower() or "complete" in line.lower()):
            proc.send_signal(signal.SIGINT)
            killed = True
            break
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    state_path = work_dir / "state.json"
    segments_done_after_kill = 0
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text())
            segs = st.get("segments", {})
            segments_done_after_kill = sum(1 for s in segs.values() if s.get("status") == "done")
        except Exception:
            pass

    # Pass 2: resume
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s,
        cwd=str(REPO),
    )
    dt = time.time() - t0
    stderr = (
        f"[pass1 killed={killed} segments_done_after_kill={segments_done_after_kill}]\n"
        + "".join(pass1_log[-30:])
        + "\n[pass2 exit=" + str(res.returncode) + "]\n"
        + (res.stderr or "")
    )
    (cell_dir / "stderr.log").write_text(stderr)

    qa_path = out_file.with_suffix(".mp4.qa.json")
    if not qa_path.exists():
        qa_path = out_file.with_name(out_file.stem + ".qa.json")

    # Sanity: pass2 must have at least kept the killed segments done
    integrity_ok = segments_done_after_kill == 0 or (out_file.exists() and res.returncode == 0)
    if not integrity_ok:
        stderr = "[RESUME INTEGRITY FAIL]\n" + stderr

    return Cell(
        input_path=str(inp),
        input_class=_probe_class(inp),
        profile=profile,
        encoder=encoder,
        workers=1,
        mode="resume",
        exit_code=res.returncode if integrity_ok else 99,
        duration_s=round(dt, 2),
        cid_predict_self=_qa_metrics(qa_path)[0],
        ssim_mean=_qa_metrics(qa_path)[1],
        vmaf_mean=_qa_metrics(qa_path)[2],
        phash_similarity=_qa_metrics(qa_path)[3],
        qa_path=str(qa_path) if qa_path.exists() else "",
        stderr_tail=_stderr_tail(stderr),
        out_path=str(out_file) if out_file.exists() else "",
    )


@app.command()
def main(
    inputs_dir: Path = typer.Option(..., "--inputs-dir", exists=True, file_okay=False),
    profiles: str = typer.Option("soft,medium", "--profiles"),
    encoders: str = typer.Option("libx264", "--encoders"),
    workers_list: str = typer.Option("1", "--workers-list"),
    include_resume: bool = typer.Option(False, "--include-resume"),
    generate_corpus: bool = typer.Option(False, "--generate-corpus"),
    timeout_s: int = typer.Option(600, "--timeout-s"),
    out_dir: Path = typer.Option(..., "--out-dir"),
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    if generate_corpus:
        from tools._corpus_gen import generate_all  # type: ignore
        print(f"[corpus] generating into {inputs_dir}", flush=True)
        for p, k in generate_all(inputs_dir):
            print(f"[corpus]  {k:>10s}  {p.name}", flush=True)

    inputs = sorted(p for p in inputs_dir.iterdir()
                    if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"})
    if not inputs:
        typer.echo(f"No inputs in {inputs_dir}", err=True)
        raise typer.Exit(1)

    profile_list = [p.strip() for p in profiles.split(",") if p.strip()]
    encoder_list = [e.strip() for e in encoders.split(",") if e.strip()]
    workers_l = [int(w.strip()) for w in workers_list.split(",") if w.strip()]

    summary_csv = out_dir / "summary.csv"
    fieldnames = [f.name for f in Cell.__dataclass_fields__.values()]  # type: ignore
    with summary_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

    total = len(inputs) * len(profile_list) * len(encoder_list) * len(workers_l)
    print(f"[matrix] {total} cells; out={out_dir}", flush=True)

    n = 0
    for inp in inputs:
        for profile in profile_list:
            for encoder in encoder_list:
                for w in workers_l:
                    n += 1
                    print(f"[{n}/{total}] {inp.name} {profile} {encoder} w={w} ...",
                          flush=True)
                    cell = _run_cell(inp, profile, encoder, w, out_dir, timeout_s)
                    with summary_csv.open("a", newline="") as fh:
                        csv.DictWriter(fh, fieldnames=fieldnames).writerow(asdict(cell))
                    print(f"      exit={cell.exit_code} dt={cell.duration_s}s "
                          f"cid={cell.cid_predict_self} ssim={cell.ssim_mean} "
                          f"class={cell.input_class}", flush=True)

    if include_resume:
        # Pick longest clip for resume cell
        longest = max(inputs, key=lambda p: p.stat().st_size)
        for profile in profile_list[:1]:
            print(f"[resume] {longest.name} {profile} libx264 ...", flush=True)
            cell = _run_resume_cell(longest, profile, "libx264", out_dir,
                                    timeout_s=timeout_s * 2)
            with summary_csv.open("a", newline="") as fh:
                csv.DictWriter(fh, fieldnames=fieldnames).writerow(asdict(cell))
            print(f"      exit={cell.exit_code} dt={cell.duration_s}s", flush=True)

    print(f"[matrix] done. summary: {summary_csv}", flush=True)


if __name__ == "__main__":
    app()
