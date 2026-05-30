"""Synthetic corpus generator for real-video bug hunt.

Each generator returns an existing Path. Idempotent: skip if file exists.
Uses ffmpeg lavfi exclusively so no binary fixtures are committed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

FF = "ffmpeg"
LOG = ["-loglevel", "error", "-hide_banner", "-y"]


def _run(args: list[str]) -> None:
    res = subprocess.run([FF, *LOG, *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args)}\n{res.stderr[-2000:]}")


def gen_sdr_4k(out: Path, duration: int = 12) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def gen_hdr10(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "mandelbrot=size=1920x1080:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx265", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p10le",
        "-x265-params",
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10=1:hdr10-opt=1",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
        "-c:a", "aac", "-b:a", "128k", "-tag:v", "hvc1", str(out),
    ])
    return out


def gen_hlg(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx265", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p10le",
        "-x265-params",
        "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        "-c:a", "aac", "-b:a", "128k", "-tag:v", "hvc1", str(out),
    ])
    return out


def gen_odd_dim(out: Path, duration: int = 10) -> Path:
    """Odd width/height: triggers even-dimensions guard."""
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1281x721:rate=30,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


def gen_60fps(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


def gen_2398fps(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24000/1001,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


def gen_vfr(out: Path, duration: int = 10) -> Path:
    """Variable frame rate via setpts expression."""
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-vf", "setpts='PTS*(1+0.3*sin(N/15))'",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-vsync", "vfr",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


def gen_audio_5_1(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25,format=yuv420p",
        "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000,aformat=channel_layouts=5.1",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "384k", "-ac", "6", str(out),
    ])
    return out


def gen_audio_mono(out: Path, duration: int = 10) -> Path:
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out),
    ])
    return out


def gen_audio_hot(out: Path, duration: int = 10) -> Path:
    """Loud / clipped-edge: loudnorm two-pass edge case."""
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25,format=yuv420p",
        "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=44100,volume=20dB",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


def gen_audio_quiet(out: Path, duration: int = 10) -> Path:
    """Very quiet: loudnorm measurement edge."""
    if out.exists():
        return out
    _run([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25,format=yuv420p",
        "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=44100,volume=-40dB",
        "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(out),
    ])
    return out


CORPUS: dict[str, tuple[callable, str]] = {
    "synth_sdr_4k.mp4":     (gen_sdr_4k,      "sdr_4k"),
    "synth_hdr10.mp4":      (gen_hdr10,       "hdr10"),
    "synth_hlg.mp4":        (gen_hlg,         "hlg"),
    "synth_odd_dim.mp4":    (gen_odd_dim,     "odd_dim"),
    "synth_60fps.mp4":      (gen_60fps,       "60fps"),
    "synth_2398fps.mp4":    (gen_2398fps,     "23.98fps"),
    "synth_vfr.mp4":        (gen_vfr,         "vfr"),
    "synth_audio_5_1.mp4":  (gen_audio_5_1,   "5.1"),
    "synth_audio_mono.mp4": (gen_audio_mono,  "mono"),
    "synth_audio_hot.mp4":  (gen_audio_hot,   "hot"),
    "synth_audio_quiet.mp4":(gen_audio_quiet, "quiet"),
}


def generate_all(dest_dir: Path) -> list[tuple[Path, str]]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[tuple[Path, str]] = []
    for name, (fn, klass) in CORPUS.items():
        path = dest_dir / name
        fn(path)
        out.append((path, klass))
    return out


if __name__ == "__main__":
    import sys
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/.gen")
    for p, k in generate_all(dest):
        print(f"{k:>10s}  {p}")
