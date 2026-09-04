# yt-uniquifier

A production-grade re-encoder for **your own** owned/licensed video
content. Wraps `ffmpeg` and applies controlled micro-transforms —
crop + rescale, color jitter, noise, rotation, frame-blend,
pitch/tempo, EQ, EBU R128 loudnorm — so a re-upload of *your* work
is a fresh, properly encoded artifact rather than a bit-identical
copy of an earlier render.

!!! warning "Scope"
    `yt-uniquifier` is for **legitimate** use only — re-uploading
    your own content, fair-use derivatives, re-cuts. The transforms
    are not designed to defeat Content ID, and the documentation
    will not help you do so. Preserve that framing in any
    contribution.

## Why use it

* **One filter chain per segment.** No bgr24 round-trip through
  Python stdin — `core/pipeline.py` builds a single
  `-filter_complex` per re-encode.
* **Crash-resumable at segment granularity.** A crash during a
  multi-hour run resumes at the segment boundary that was in
  progress, not from scratch.
* **Multi-vendor encoder real-probe.** NVENC, QSV, AMF,
  VideoToolbox, x264, x265 are each test-fired against a `lavfi`
  null source and cached — *available* on the box and *works* are
  treated as different things.
* **HDR-aware micro-transforms.** Color jitter happens inside a
  zscale linear-light wrap; tonemap_sdr is gated on the source
  actually being HDR.
* **Distributed batch over a shared filesystem.** No Redis, no
  Postgres — atomic `os.rename` across workers.
* **Headless web UI + Docker image.** Drop the container on a NAS
  and drive encodes from a browser.

## Quick install (CLI)

```bash
pip install yt-uniquifier              # core + CLI
pip install yt-uniquifier[gui]          # adds the PyQt6 desktop GUI
pip install yt-uniquifier[web]          # adds the FastAPI web UI
yt-uniq version                       # `--version` is also supported
```

Requires `ffmpeg` / `ffprobe` on `PATH`. Optional binaries:
`fpcalc` (chromaprint), ffmpeg with `libvmaf`, `whisper-cpp` (for
subtitle generation). See [Install](install.md) for full notes.

## First encode in 60 seconds

```bash
yt-uniq probe input.mp4                                   # see what it is
yt-uniq preflight input.mp4 --profile cid_aware           # sanity-check
yt-uniq run input.mp4 --profile cid_aware --output out.mp4
```

A fuller tour lives at [Getting started](getting-started.md).

## What's new in v0.9

* **Community profile marketplace** — browse and install YAML
  profiles from a curated, SHA-pinned catalog without leaving
  the CLI/GUI. [Read more →](marketplace.md)
* **Whisper subtitle transform** — burn pre-generated SRT/ASS
  into the video; `yt-uniq subtitles generate` produces the SRT
  via whisper.cpp. [Read more →](profiles.md)
* **Opt-in local telemetry** — one anonymous summary event per
  run, JSONL on disk, no network. [Read more →](telemetry.md)
* **Headless FastAPI web UI + Docker image** — drive encodes
  from a browser; baked-in ffmpeg, non-root container, basic
  auth via env vars. [Read more →](web.md)
* **Localization (en + ru)** — hot-swappable in Settings →
  Appearance → Language. [Read more →](i18n.md)

The full v0.9 roadmap and round-by-round breakdown live at
`specs/v0.9-plan.md` in the repo.
